from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from absulli import __version__
from absulli.core.config import get_settings
from absulli.core.logging import configure_logging
from absulli.database.session import init_db
from absulli.core.security import SecurityHeadersMiddleware
from absulli.core.setup_state import is_setup_complete
from absulli.monitors.scheduler import AbsulliScheduler
from absulli.web.api import metrics_router, router as api_router
from absulli.web.routes import router as web_router

settings = get_settings()
configure_logging(settings.log_level)
scheduler = AbsulliScheduler(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.start()
    yield
    await scheduler.shutdown()


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)

if settings.cors_allowed_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins_list,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allowed_methods_list,
        allow_headers=settings.cors_allowed_headers_list,
    )

app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory="absulli/web/static"), name="static")
app.include_router(web_router)
app.include_router(api_router)
app.include_router(metrics_router)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "setup_required": not is_setup_complete()}
