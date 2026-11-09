"""AgentCanvas API application factory.

uvicorn agentcanvas.api.main:app --reload          # real models
uvicorn agentcanvas.dev.fake_server:app --reload   # scripted fake models (no keys)
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from structlog.contextvars import bind_contextvars, clear_contextvars

from agentcanvas import observability, version
from agentcanvas.adapters import get_adapter, installed_adapters, model_service
from agentcanvas.adapters.langchain import interop
from agentcanvas.adapters.langchain.answer_cache import DatabaseAnswerCache
from agentcanvas.adapters.langchain.persistence import MemoryEmbedding
from agentcanvas.adapters.spi import MemoryAbility, require
from agentcanvas.api.audit import log_refusal
from agentcanvas.api.audit import record as audit_record
from agentcanvas.api.deps import release_sessions
from agentcanvas.api.limits import BodyLimit, RefuseClients
from agentcanvas.api.problems import install_problem_handlers, problem_responses, unexpected_error
from agentcanvas.api.scim import router as scim_router
from agentcanvas.api.shutdown import WindDown
from agentcanvas.api.v1 import router as v1_router
from agentcanvas.api.v1 import runs as runs_module
from agentcanvas.db.repositories import DEV_ORG, retire_dev_user, seed_dev
from agentcanvas.db.rls import bypasses_policies, sees_no_rows
from agentcanvas.db.session import Database
from agentcanvas.ids import new_id
from agentcanvas.log import configure_logging, get_logger
from agentcanvas.registry import default_registry
from agentcanvas.runtime import masking, nesting
from agentcanvas.runtime import paused as paused_runs
from agentcanvas.runtime.admission import Admission
from agentcanvas.runtime.paused import DatabasePaused, DatabaseReplies, MemoryPaused
from agentcanvas.runtime.runner import Runner
from agentcanvas.security.auth import check_auth_settings
from agentcanvas.security.kms import Keyring
from agentcanvas.services import oauth, routing
from agentcanvas.services import projects as projects_module
from agentcanvas.services.audit_sink import sink_for
from agentcanvas.services.callbacks import CallbackJob
from agentcanvas.services.events import EventService
from agentcanvas.services.experiments import ExperimentJob
from agentcanvas.services.files import FileStore
from agentcanvas.services.knowledge import CrawlJob, Searcher, knowledge_embedder
from agentcanvas.services.matura import MaturaFeed
from agentcanvas.services.online import OnlineScorer
from agentcanvas.services.org_settings import OrgSettingsCache
from agentcanvas.services.presence import PresenceHub
from agentcanvas.services.projects import DEFAULT_PROJECT, ProjectRegistry
from agentcanvas.services.ratelimit import RateLimiter
from agentcanvas.services.redeploy import HandoverJob
from agentcanvas.services.retention import RetentionJob
from agentcanvas.services.run_gate import RunGate
from agentcanvas.services.run_search import RunSearchService
from agentcanvas.services.secrets import SecretCache, encoded_forms
from agentcanvas.services.spend_caps import SpendCaps
from agentcanvas.services.trace_export import Exporter, TraceExportHook
from agentcanvas.services.triggers import CronScheduler
from agentcanvas.settings import Settings, get_settings

log = get_logger(__name__)


def sandbox_hint(settings: Settings) -> str | None:
    """A warning for a staging or production server whose workspace code is not isolated from the API."""
    if settings.env in ("staging", "prod") and settings.script_execution in ("inprocess", "subprocess"):
        return (
            "workspace code runs as the API's user and can read its files; set AGENTCANVAS_SCRIPT_EXECUTION=remote "
            "(the sandbox service), docker or langsmith when people you do not trust can edit workspaces"
        )
    return None


def kms_hint(settings: Settings) -> str | None:
    """A warning for a staging or production server whose secrets master key is derived from its secret key."""
    if settings.env in ("staging", "prod") and not settings.kms_key:
        return (
            "AGENTCANVAS_KMS_KEY is empty, so the master key of project secrets is derived from "
            "AGENTCANVAS_SECRET_KEY: "
            "changing that key makes every secret unreadable unless the old one is kept in "
            "AGENTCANVAS_KMS_PREVIOUS_KEYS. Set AGENTCANVAS_KMS_KEY (file:, aws: or gcp:); see the deployment guide"
        )
    return None


# the plan's work-item ids ("(S17)", "(M7-T3)", "(FR-X-13)") keep the code traceable; the API's readers do not need them
_REF = r"(?:S\d+|M\d+-T\d+|T\d+|FR-[A-Z]+-\d+|SP-\d+|TS\d+)"
_REFS = re.compile(rf"\s*\({_REF}(?:[ ,;/]+{_REF})*\)")


def plain_openapi(app: FastAPI) -> dict[str, Any]:
    """The OpenAPI document with the planning ids taken out of its descriptions (cached like FastAPI's own)."""
    if app.openapi_schema is None:
        from fastapi.openapi.utils import get_openapi

        doc = get_openapi(title=app.title, version=app.version, routes=app.routes)

        def clean(node: object) -> object:
            if isinstance(node, dict):
                return {k: _REFS.sub("", v) if k in ("description", "summary") and isinstance(v, str) else clean(v)
                        for k, v in node.items()}  # fmt: skip
            if isinstance(node, list):
                return [clean(v) for v in node]
            return node

        app.openapi_schema = problem_responses(cast("dict[str, Any]", clean(doc)))
    return app.openapi_schema


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(json=settings.log_json)
    check_auth_settings(settings)
    default_registry()  # a malformed AGENTCANVAS_MODEL_PRICES stops the start, not the first run that needs a price

    def _not_dumpable() -> None:
        """Linux: /proc/<this pid>/environ, mem and maps become readable by root only, and ptrace is refused, so a
        workspace script in a `subprocess` sandbox (same user) cannot read the database URL or keys from them."""
        if sys.platform != "linux":
            return
        try:
            import ctypes

            pr_set_dumpable = 4
            ctypes.CDLL(None, use_errno=True).prctl(pr_set_dumpable, 0, 0, 0, 0)
        except (OSError, AttributeError):
            log.warning("sandbox.dumpable_not_cleared")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_url, settings.db_pool_size, settings.db_max_overflow)
        if settings.auto_migrate:
            await db.migrate()
        else:
            await db.check_migrated()  # migrated by someone else: refuse a database they have not reached yet
        if await sees_no_rows(db.engine):  # otherwise every page is empty and cron, hand-over and retention idle
            raise RuntimeError(
                "the API's database connections do not start with agentcanvas.all_tenants=on, so row-level security "
                "hides every row: a connection pooler dropped the startup option. Run `python -m "
                "agentcanvas.db.app_role` as the owner (it gives the API's role that default), or connect directly"
            )
        if settings.seed_dev_data:
            async with db.sessionmaker() as s:
                await seed_dev(s)
        if settings.auth_mode in ("oidc", "matura"):
            async with db.sessionmaker() as s:
                await retire_dev_user(s)
        if settings.auth_mode == "matura" and await bypasses_policies(db.engine):
            # row-level security is the tenant boundary in Matura mode; a superuser or BYPASSRLS role skips it
            hint = "connect the API as an ordinary role (NOSUPERUSER NOBYPASSRLS); keep the owner for migrations"
            if settings.env in ("staging", "prod"):
                raise RuntimeError(f"the database role bypasses row-level security: {hint}")
            log.warning("rls.bypassed", hint=hint)
        app.state.db = db
        paused_runs.install(DatabasePaused(db))  # paused runs of every replica, kept across restarts
        interop.install_replies(DatabaseReplies(db))  # and what remote A2A agents answered paused steps (G-9)
        if hint := sandbox_hint(settings):
            log.warning("sandbox.not_isolated", mode=settings.script_execution, hint=hint)
        if hint := kms_hint(settings):
            log.warning("kms.derived_from_secret_key", hint=hint)
        if settings.ignore_org_networks:
            log.warning(
                "networks.ignored", hint="organisations' IP allow-lists are off: unset AGENTCANVAS_IGNORE_ORG_NETWORKS"
            )
        if settings.script_execution == "subprocess":
            _not_dumpable()  # sandbox children run as this user: keep them from reading this process's memory
        observability.trace_engine(db.engine)
        adapter = get_adapter("langchain")  # the host framework: long-term memory, MCP browsing, model resolution
        others = {i: a for i, a in installed_adapters().items() if i != adapter.id}  # S20: other frameworks
        async with contextlib.AsyncExitStack() as stack:
            # the host framework's long-term memory gets a semantic index when the server names a model (F-7);
            # the adapter outlives this app, so a server without one clears the previous app's
            cast(Any, adapter).memory_embedding = (
                MemoryEmbedding(
                    settings.memory_embedding,
                    settings.memory_embedding_dims,
                    settings.memory_embedding_base_url or None,
                )
                if settings.memory_embedding
                else None
            )
            await stack.enter_async_context(adapter.persistence(settings.persistence_url or settings.database_url))
            for other in others.values():
                await stack.enter_async_context(other.persistence(settings.persistence_url or settings.database_url))
            app.state.models = model_service()
            if moved := await require(adapter, "memory_store", MemoryAbility).memory_adopt(
                DEV_ORG
            ):  # memories from before organisations (M7-T2)
                log.info("memory.adopted", org_id=DEV_ORG, items=moved)
            app.state.projects = ProjectRegistry(settings, adapter, getattr(app.state, "resources_class", None))
            projects_module.CURRENT = app.state.projects
            app.state.keyring = Keyring.from_settings(settings)
            hosted = settings.edition == "hosted"
            app.state.oauth = oauth.Connections(
                app.state.keyring, settings.public_url or next(iter(settings.cors_origins), ""), public_only=hosted
            )  # sign-ins to app connectors (G-7); with no address for the web app, a sign-in says so when it starts
            app.state.oauth_tokens = oauth.ConnectionTokens(db, app.state.keyring, public_only=hosted)
            oauth.install(app.state.oauth_tokens)
            nesting.install(settings.secret_key)  # runs' A2A and MCP calls name the run (one org's nested runs)
            masking.provider = lambda: (  # traces: each secret and its encoded forms (S-9)
                f for values in app.state.projects.secrets.values() for v in values.values() for f in encoded_forms(v)
            )
            masking.install_langsmith()
            # decrypted at startup, reloaded when another instance changed them (each project's secrets_version)
            app.state.secrets = SecretCache(db, lambda: app.state.keyring, app.state.projects.secrets)
            await app.state.secrets.sync()
            # S24: Search knowledge blocks read this server's knowledge bases
            app.state.projects.knowledge = Searcher(
                db, knowledge_embedder(adapter), lambda project_id: app.state.projects.secrets.get(project_id, {})
            )
            app.state.crawl = CrawlJob(
                db,
                knowledge_embedder(adapter),
                lambda project_id: app.state.projects.secrets.get(project_id, {}),
                transport=lambda: getattr(app.state, "knowledge_transport", None),
            )
            app.state.projects.answer_cache = lambda project_id: DatabaseAnswerCache(db, project_id)
            app.state.projects.files = FileStore(db)  # G-5: file fields are read from the project's files
            runner = Runner(
                db,
                adapter,
                lambda thread_id, wf: _resources(app, thread_id, wf),
                adapters=others,
                models=app.state.models,
            )
            app.state.runner = runner
            runner.router = lambda session, wf, project_id: routing.route_run(app, session, wf, project_id)
            runner.max_file_bytes = int(settings.max_upload_mb * 1024 * 1024)
            runner.max_project_file_bytes = int(settings.max_project_files_mb * 1024 * 1024)
            runner.prepare_hooks.append(
                lambda wf: app.state.secrets.refresh(projects_module.PROJECT_OF.get(wf.id, DEFAULT_PROJECT))
            )
            # every run, whatever started it (triggers, events, hand-over), passes the security rules first
            app.state.org_settings = OrgSettingsCache(db)

            async def concurrent_limit(project_id: str | None) -> int:
                return (await app.state.org_settings.for_project(settings, project_id)).max_concurrent_runs

            runner.admission = Admission(
                db, concurrent_limit, stale_after_s=settings.orphan_after_s, max_wait_s=settings.max_queue_wait_s
            )
            # what an organisation or project may spend a day and a month: new runs are refused and runs stop at a cap
            app.state.spend_caps = runner.caps = SpendCaps(db, settings, ttl=0 if settings.env == "test" else 5.0)
            app.state.spend_caps.sink = lambda: getattr(app.state, "audit_sink", None)
            app.state.spend_caps.alert_hooks.append(observability.spend_cap_alert)
            runner.prepare_hooks.append(
                RunGate(
                    settings,
                    lambda wf: app.state.projects.registry(projects_module.PROJECT_OF.get(wf.id, DEFAULT_PROJECT)),
                    lambda wf: app.state.org_settings.for_project(
                        settings, projects_module.PROJECT_OF.get(wf.id, DEFAULT_PROJECT)
                    ),
                    lambda wf: app.state.projects.workspace(
                        projects_module.PROJECT_OF.get(wf.id, DEFAULT_PROJECT)
                    ).revision(),
                )
            )
            app.state.events = EventService(db, runner, app.state.projects)
            runner.after_hooks.append(app.state.events.on_run_finished)
            runner.start_hooks.append(observability.run_started)
            runner.admission.left_hooks.append(observability.queue_left)
            observability.RUNS_QUEUED.set_function(runner.admission.waiting)
            runner.finish_hooks.append(observability.run_finished)
            runner.alert_hooks.append(observability.cost_alert)
            # tests check drift after every score; servers at most every few seconds per evaluator
            app.state.online = OnlineScorer(db, runner, drift_every_s=0 if settings.env == "test" else 10.0)
            runner.finish_hooks.append(app.state.online.on_run_finished)
            # G-10: finished runs are embedded for "find conversations where …" (the project's embedding model)
            app.state.run_search = RunSearchService(
                db, knowledge_embedder(adapter), lambda project_id: app.state.projects.secrets.get(project_id, {})
            )
            runner.finish_hooks.append(app.state.run_search.on_run_finished)
            app.state.presence = PresenceHub(db)  # G-4: who has a workflow open
            app.state.trace_export = TraceExportHook(
                db, app.state.projects, Exporter(public_only=settings.edition == "hosted")
            )
            runner.finish_hooks.append(app.state.trace_export.on_run_finished)
            # E-2: deployment runs' completion callbacks, delivered by every server
            app.state.callbacks = CallbackJob(db, runner, app.state.keyring, public_only=settings.edition == "hosted")
            runner.finish_hooks.append(app.state.callbacks.on_run_finished)
            app.state.cron = CronScheduler(db, runner, app.state.keyring, catchup_s=settings.cron_catchup_s)
            app.state.ratelimit = RateLimiter(db)
            app.state.retention = RetentionJob(
                db, runner, settings.retention_days, settings.retention_interval_s,
                audit_days=settings.audit_retention_days, memory_days=settings.memory_retention_days, server=settings,
            )  # fmt: skip

            async def handover_target(s, thread_id, version_id):
                return await runs_module.handover_target(s, settings, thread_id, version_id)

            app.state.handover = HandoverJob(
                db, runner, handover_target, settings.handover_interval_s, settings.orphan_after_s
            )
            app.state.experiments = ExperimentJob(db, runner, settings.handover_interval_s, settings.orphan_after_s)
            if settings.env != "test":  # tests drive the schedulers with explicit ticks
                app.state.experiments.start()
                app.state.cron.start()
                app.state.retention.start()
                app.state.crawl.start()
                app.state.handover.start()
                app.state.callbacks.start()
                app.state.online.start()
                app.state.secrets.start()
            app.state.matura_feed = None
            if settings.auth_mode == "matura" and settings.matura_url:
                app.state.matura_feed = MaturaFeed(db, settings)
                if settings.env != "test":
                    app.state.matura_feed.start()
            app.state.audit_sink = sink_for(settings)  # TS26: security-relevant entries also leave the app
            if settings.env in ("staging", "prod") and not os.getenv("LANGGRAPH_AES_KEY"):
                log.warning("checkpoints.unencrypted", hint="set LANGGRAPH_AES_KEY (16, 24 or 32 characters)")
            log.info(
                "api.started", version=version.VERSION, commit=version.COMMIT, env=settings.env,
                edition=settings.edition, auth=settings.auth_mode, database=db.engine.dialect.name,
            )  # fmt: skip

            async def wind_down() -> None:
                """From the stop signal on (api/shutdown.py): no more work starts here, and the runs drain."""
                app.state.presence.close()  # endless streams end: uvicorn's wait for connections need not last
                await app.state.experiments.stop()  # before the drain: its runs are handed over with the rest
                await app.state.cron.stop()
                await app.state.retention.stop()
                await app.state.crawl.stop()
                await app.state.secrets.stop()
                if app.state.matura_feed is not None:
                    await app.state.matura_feed.stop()
                await runner.drain(settings.drain_grace_s)  # FR-X-10: other instances continue what is left
                # after the drain: its ticks keep the heartbeat of runs finishing in the grace period (a draining
                # server neither sweeps nor claims), so no other server takes them for orphaned and runs them twice
                await app.state.handover.stop()
                await app.state.callbacks.stop()  # after the drain: the runs that ended in it are called back

            app.state.wind_down = WindDown(wind_down)
            try:
                with app.state.wind_down.on_signals():
                    yield
            finally:
                oauth.install(None)
                nesting.install(None)
                await app.state.wind_down.begin()  # begun at the signal already, or now
                # after the drain: runs finishing in it are scored and indexed too, both at once (AFTER_DRAIN_S)
                await asyncio.gather(app.state.online.stop(), app.state.run_search.drain())
                await runner.shutdown()
                await asyncio.to_thread(app.state.trace_export.exporter.shutdown)  # flushes queued traces
                await app.state.projects.aclose()
                if hasattr(app.state.audit_sink, "aclose"):  # tests may put a recorder in its place
                    await app.state.audit_sink.aclose()
                await db.dispose()
                paused_runs.install(MemoryPaused())  # this database is gone
                interop.install_replies(interop.MemoryReplies())

    # interactive docs and the schema stay off in production: they map the attack surface (S15)
    docs: dict = {"docs_url": None, "redoc_url": None, "openapi_url": None} if settings.env == "prod" else {}
    app = FastAPI(title="AgentCanvas API", version=version.VERSION, lifespan=lifespan, **docs)
    app.state.settings = settings
    app.openapi = lambda: plain_openapi(app)  # type: ignore[method-assign]
    install_problem_handlers(app)
    observability.setup_tracing(app)
    app.add_middleware(BodyLimit)  # 413 for bodies over 20 MB, whoever reaches the API
    if settings.refuse_clients:
        app.add_middleware(RefuseClients, networks=settings.refuse_clients)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["ETag", "Location", "x-request-id"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or new_id("req")
        clear_contextvars()
        bind_contextvars(request_id=request_id)
        observability.tag_current_span(**{"request.id": request_id})
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # a bug: the `Exception` handler answers outside this middleware, so the crash would miss the metric,
            # the header and the structured log (Starlette's traceback is plain text, unmasked, without the id).
            # Answer it here; nothing escapes, so the server logs it once, as JSON.
            log.exception("http.unhandled", method=request.method, route=observability.route_label(request.scope))
            response = unexpected_error(request_id)
        await release_sessions(request)  # streams keep no database connection
        route = observability.route_label(request.scope)
        observability.observe_request(request.method, route, response.status_code, time.perf_counter() - start)
        await audit_record(request, response)
        log_refusal(request, response)
        response.headers["x-request-id"] = request_id
        log.debug(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=round((time.perf_counter() - start) * 1000, 1),
        )
        return response

    if settings.metrics_enabled:

        @app.get("/metrics", tags=["ops"], include_in_schema=False)
        def metrics() -> Response:
            """Prometheus metrics (M8-T3); scrape the API directly, the web image does not proxy it."""
            body, content_type = observability.metrics_response()
            return Response(body, media_type=content_type)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"], responses={503: {"description": "a dependency does not answer"}})
    async def readyz(request: Request) -> JSONResponse:
        """Readiness probe: the control database and the run store (checkpoints, memory) answer within 2 s."""

        async def database() -> None:
            async with request.app.state.db.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

        async def store() -> None:
            await request.app.state.runner.adapter.store.alist_namespaces(limit=1)

        if request.app.state.runner.draining:  # shutting down: send no more traffic here (FR-X-10)
            return JSONResponse({"status": "draining"}, status_code=503)
        failed = []
        for name, check in (("database", database), ("store", store)):
            try:
                await asyncio.wait_for(check(), 2)
            except Exception as exc:  # noqa: BLE001 - any failure means not ready
                log.warning("readyz.failed", dependency=name, error=repr(exc))
                failed.append(name)
        if failed:
            return JSONResponse({"status": "unavailable", "failed": failed}, status_code=503)
        return JSONResponse({"status": "ready"})

    app.include_router(v1_router)
    app.include_router(scim_router)  # /scim/v2: the organisation's SCIM token (G-13)
    return app


def _resources(app: FastAPI, thread_id: str, wf):
    """Model/tool/script resolver for a thread: the workflow's project context. Tests may install
    `app.state.resources_factory`; the key-less dev server sets `app.state.resources_class`."""
    factory = getattr(app.state, "resources_factory", None)
    return factory(thread_id, wf) if factory else app.state.projects.for_workflow(wf)


app = create_app()
