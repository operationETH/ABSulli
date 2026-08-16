from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from fastapi.templating import Jinja2Templates

from absulli import __version__
from absulli.core.config import get_settings
from absulli.core.time import format_local_date, format_local_datetime, format_local_time
from absulli.web.update_check import update_status

STATIC_ROOT = Path(__file__).resolve().parent / "static"


@lru_cache(maxsize=None)
def static_asset_version(asset_path: str) -> str:
    path = (STATIC_ROOT / asset_path).resolve()
    if not path.is_relative_to(STATIC_ROOT.resolve()) or not path.is_file():
        return __version__
    return sha256(path.read_bytes()).hexdigest()[:12]


def update_status_context(request):
    return {"update_status": update_status(get_settings(), __version__)}


templates = Jinja2Templates(
    directory="absulli/web/templates",
    context_processors=[update_status_context],
)
templates.env.globals["absulli_version"] = __version__
templates.env.globals["static_asset_version"] = static_asset_version
templates.env.filters["local_date"] = format_local_date
templates.env.filters["local_datetime"] = format_local_datetime
templates.env.filters["local_time"] = format_local_time
