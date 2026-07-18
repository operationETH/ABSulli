from fastapi.templating import Jinja2Templates

from absulli import __version__
from absulli.core.time import format_local_date, format_local_datetime, format_local_time

templates = Jinja2Templates(directory="absulli/web/templates")
templates.env.globals["absulli_version"] = __version__
templates.env.filters["local_date"] = format_local_date
templates.env.filters["local_datetime"] = format_local_datetime
templates.env.filters["local_time"] = format_local_time
