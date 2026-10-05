"""AgentCanvas API application factory.

uvicorn agentcanvas.api.main:app --reload          # real models
uvicorn agentcanvas.dev.fake_server:app --reload   # scripted fake models (no keys)
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from structlog.contextvars import bind_contextvars, clear_contextvars

from agentcanvas import observability
from agentcanvas.adapters import get_adapter
from agentcanvas.api.audit import record as audit_record
from agentcanvas.api.problems import install_problem_handlers
from agentcanvas.api.v1 import router as v1_router
from agentcanvas.db.repositories import DEV_ORG, retire_dev_user, seed_dev
from agentcanvas.db.session import Database
from agentcanvas.ids import new_id
from agentcanvas.log import configure_logging, get_logger
from agentcanvas.runtime.runner import Runner
from agentcanvas.security.auth import check_auth_settings
from agentcanvas.services import projects as projects_module
from agentcanvas.services.events import EventService
from agentcanvas.services.projects import ProjectRegistry
from agentcanvas.services.secrets import SecretService
from agentcanvas.services.triggers import CronScheduler
from agentcanvas.settings import Settings, get_settings

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(json=settings.log_json)
    check_auth_settings(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_url)
        if settings.auto_migrate:
            await db.migrate()
        if settings.seed_dev_data:
            async with db.sessionmaker() as s:
                await seed_dev(s)
        if settings.auth_mode == "oidc":
            async with db.sessionmaker() as s:
                await retire_dev_user(s)
        app.state.db = db
        observability.trace_engine(db.engine)
        adapter = get_adapter("langchain")
        async with adapter.persistence(settings.persistence_url or settings.database_url):
            if moved := await adapter.memory_adopt(DEV_ORG):  # memories from before organisations (M7-T2)
                log.info("memory.adopted", org_id=DEV_ORG, items=moved)
            app.state.projects = ProjectRegistry(settings, adapter, getattr(app.state, "resources_class", None))
            projects_module.CURRENT = app.state.projects
            async with db.sessionmaker() as s:  # decrypted once at startup; the secrets API keeps them in sync
                for project, values in (await SecretService(s, settings.secret_key).values()).items():
                    app.state.projects.secrets.setdefault(project, {}).update(values)
            runner = Runner(db, adapter, lambda thread_id, wf: _resources(app, thread_id, wf))
            app.state.runner = runner
            app.state.events = EventService(db, runner, app.state.projects)
            runner.finish_hooks.append(app.state.events.on_run_finished)
            runner.start_hooks.append(observability.run_started)
            runner.finish_hooks.append(observability.run_finished)
            app.state.cron = CronScheduler(db, runner, settings.secret_key)
            if settings.env != "test":  # tests drive the scheduler with explicit ticks
                app.state.cron.start()
            log.info(
                "api.started", env=settings.env, edition=settings.edition, auth=settings.auth_mode,
                database=db.engine.dialect.name,
            )  # fmt: skip
            try:
                yield
            finally:
                await app.state.cron.stop()
                await runner.shutdown()
                await app.state.projects.aclose()
                await db.dispose()

    app = FastAPI(title="AgentCanvas API", version="0.1.0", lifespan=lifespan)
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
