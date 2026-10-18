"""AgentCanvas API application factory.

uvicorn agentcanvas.api.main:app --reload          # real models
uvicorn agentcanvas.dev.fake_server:app --reload   # scripted fake models (no keys)
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from structlog.contextvars import bind_contextvars, clear_contextvars

from agentcanvas import observability
from agentcanvas.adapters import get_adapter, installed_adapters, model_service
from agentcanvas.adapters.spi import MemoryAbility, require
from agentcanvas.api.audit import record as audit_record
from agentcanvas.api.deps import release_sessions
from agentcanvas.api.problems import install_problem_handlers
from agentcanvas.api.v1 import router as v1_router
from agentcanvas.api.v1 import runs as runs_module
from agentcanvas.db.repositories import DEV_ORG, retire_dev_user, seed_dev
from agentcanvas.db.rls import bypasses_policies
from agentcanvas.db.session import Database
from agentcanvas.ids import new_id
from agentcanvas.log import configure_logging, get_logger
from agentcanvas.runtime import masking
from agentcanvas.runtime.runner import Runner
from agentcanvas.security.auth import check_auth_settings
from agentcanvas.security.kms import Keyring
from agentcanvas.services import projects as projects_module
from agentcanvas.services.audit_sink import MaturaWebhookSink, sink_for
from agentcanvas.services.events import EventService
from agentcanvas.services.experiments import ExperimentJob
from agentcanvas.services.knowledge import Searcher, knowledge_embedder
from agentcanvas.services.matura import MaturaFeed
from agentcanvas.services.online import OnlineScorer
from agentcanvas.services.projects import DEFAULT_PROJECT, ProjectRegistry
from agentcanvas.services.ratelimit import RateLimiter
from agentcanvas.services.redeploy import HandoverJob
from agentcanvas.services.retention import RetentionJob
from agentcanvas.services.run_gate import RunGate
from agentcanvas.services.secrets import SecretCache
from agentcanvas.services.trace_export import TraceExportHook
from agentcanvas.services.triggers import CronScheduler
from agentcanvas.settings import Settings, get_settings

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(json=settings.log_json)
    check_auth_settings(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_url, settings.db_pool_size, settings.db_max_overflow)
        if settings.auto_migrate:
            await db.migrate()
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
        observability.trace_engine(db.engine)
        adapter = get_adapter("langchain")  # the host framework: long-term memory, MCP browsing, model resolution
        others = {i: a for i, a in installed_adapters().items() if i != adapter.id}  # S20: other frameworks
        async with contextlib.AsyncExitStack() as stack:
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
            masking.provider = lambda: (v for values in app.state.projects.secrets.values() for v in values.values())
            masking.install_langsmith()
            # decrypted at startup, reloaded when another instance changed them (each project's secrets_version)
            app.state.secrets = SecretCache(db, lambda: app.state.keyring, app.state.projects.secrets)
            await app.state.secrets.sync()
            # S24: Search knowledge blocks read this server's knowledge bases
            app.state.projects.knowledge = Searcher(
                db, knowledge_embedder(adapter), lambda project_id: app.state.projects.secrets.get(project_id, {})
            )
            runner = Runner(
                db,
                adapter,
                lambda thread_id, wf: _resources(app, thread_id, wf),
                adapters=others,
                models=app.state.models,
            )
            app.state.runner = runner
            runner.prepare_hooks.append(
                lambda wf: app.state.secrets.refresh(projects_module.PROJECT_OF.get(wf.id, DEFAULT_PROJECT))
            )
            # every run, whatever started it (triggers, events, hand-over), passes the security rules first
            runner.prepare_hooks.append(
                RunGate(
                    settings,
                    lambda wf: app.state.projects.registry(projects_module.PROJECT_OF.get(wf.id, DEFAULT_PROJECT)),
                )
            )
            app.state.events = EventService(db, runner, app.state.projects)
            runner.after_hooks.append(app.state.events.on_run_finished)
            runner.start_hooks.append(observability.run_started)
            runner.finish_hooks.append(observability.run_finished)
            runner.alert_hooks.append(observability.cost_alert)
            # tests check drift after every score; servers at most every few seconds per evaluator
            app.state.online = OnlineScorer(db, runner, drift_every_s=0 if settings.env == "test" else 10.0)
            runner.finish_hooks.append(app.state.online.on_run_finished)
            app.state.trace_export = TraceExportHook(db, app.state.projects)
            runner.finish_hooks.append(app.state.trace_export.on_run_finished)
            app.state.cron = CronScheduler(db, runner, app.state.keyring)
            app.state.ratelimit = RateLimiter(db)
            app.state.retention = RetentionJob(
                db, runner, settings.retention_days, settings.retention_interval_s,
                audit_days=settings.audit_retention_days, memory_days=settings.memory_retention_days,
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
                app.state.handover.start()
                app.state.online.start()
                app.state.secrets.start()
            app.state.matura_feed = None
            if settings.auth_mode == "matura" and settings.matura_url:
                app.state.matura_feed = MaturaFeed(db, settings)
                if settings.env != "test":
                    app.state.matura_feed.start()
            app.state.audit_sink = sink_for(settings)  # TS26: security-relevant entries also leave the app
            if settings.env in ("staging", "prod") and not os.getenv("LANGGRAPH_AES_KEY"):
                log.warning("checkpoints.unencrypted", hint="set LANGGRAPH_AES_KEY (32 characters)")
            log.info(
                "api.started", env=settings.env, edition=settings.edition, auth=settings.auth_mode,
                database=db.engine.dialect.name,
            )  # fmt: skip
            try:
                yield
            finally:
                await app.state.experiments.stop()  # before the drain: its runs are handed over with the rest
                await app.state.handover.stop()
                await app.state.cron.stop()
                await app.state.retention.stop()
                await app.state.secrets.stop()
                if app.state.matura_feed is not None:
                    await app.state.matura_feed.stop()
                await runner.drain(settings.drain_grace_s)  # FR-X-10: other instances continue what is left
                await app.state.online.stop()  # after the drain: runs finishing in it are scored too
                await runner.shutdown()
                await asyncio.to_thread(app.state.trace_export.exporter.shutdown)  # flushes queued traces
                await app.state.projects.aclose()
                if isinstance(app.state.audit_sink, MaturaWebhookSink):
                    await app.state.audit_sink.aclose()
                await db.dispose()

    # interactive docs and the schema stay off in production: they map the attack surface (S15)
    docs: dict = {"docs_url": None, "redoc_url": None, "openapi_url": None} if settings.env == "prod" else {}
    app = FastAPI(title="AgentCanvas API", version="0.28.1", lifespan=lifespan, **docs)
    app.state.settings = settings
    install_problem_handlers(app)
    observability.setup_tracing(app)
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
        response = await call_next(request)
        await release_sessions(request)  # streams keep no database connection
        route = observability.route_label(request.scope)
        observability.observe_request(request.method, route, response.status_code, time.perf_counter() - start)
        await audit_record(request, response)
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
    return app


def _resources(app: FastAPI, thread_id: str, wf):
    """Model/tool/script resolver for a thread: the workflow's project context. Tests may install
    `app.state.resources_factory`; the key-less dev server sets `app.state.resources_class`."""
    factory = getattr(app.state, "resources_factory", None)
    return factory(thread_id, wf) if factory else app.state.projects.for_workflow(wf)


app = create_app()
