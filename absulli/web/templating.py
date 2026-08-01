from fastapi.templating import Jinja2Templates

from absulli import __version__
from absulli.core.config import get_settings
from absulli.core.time import format_local_date, format_local_datetime, format_local_time
from absulli.web.update_check import update_status

def update_status_context(request):
    return {"update_status": update_status(get_settings(), __version__)}


templates = Jinja2Templates(
    directory="absulli/web/templates",
    context_processors=[update_status_context],
)
templates.env.globals["absulli_version"] = __version__
templates.env.filters["local_date"] = format_local_date
templates.env.filters["local_datetime"] = format_local_datetime
templates.env.filters["local_time"] = format_local_time
