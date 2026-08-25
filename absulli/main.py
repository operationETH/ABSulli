from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from absulli import __version__
from absulli.core.config import get_settings
from absulli.core.cors import DynamicCORSMiddleware
from absulli.core.logging import configure_logging
from absulli.core.cover_cache import prune_cover_cache
from absulli.database.session import init_db
from absulli.core.security import SecurityHeadersMiddleware
from absulli.core.setup_state import ensure_api_token, is_setup_complete, warm_setup_state_cache
from absulli.monitors.scheduler import AbsulliScheduler
from absulli.web.api import metrics_router, router as api_router
from absulli.web.routes import router as web_router

settings = get_settings()
configure_logging(settings.log_level)
scheduler = AbsulliScheduler(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    warm_setup_state_cache()
    if settings.effective_api_enabled and not settings.effective_api_token:
        ensure_api_token()
    prune_cover_cache(settings.data_dir)
    scheduler.start()
    yield
    await scheduler.shutdown()


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)

app.add_middleware(DynamicCORSMiddleware)

app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory="absulli/web/static"), name="static")
app.include_router(web_router)
app.include_router(api_router)
app.include_router(metrics_router)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "setup_required": not is_setup_complete()}
