from starlette.middleware.cors import CORSMiddleware

from absulli.core.config import get_settings


class DynamicCORSMiddleware:
    def __init__(self, app):
        self.app = app
        self._signature = None
        self._cors_app = app

    def _current_app(self):
        settings = get_settings()
        origins = tuple(settings.cors_allowed_origins_list)
        signature = (
            origins,
            settings.effective_cors_allow_credentials,
            tuple(settings.cors_allowed_methods_list),
            tuple(settings.cors_allowed_headers_list),
        )

        if signature != self._signature:
            self._signature = signature
            if origins:
                self._cors_app = CORSMiddleware(
                    self.app,
                    allow_origins=list(origins),
                    allow_credentials=settings.effective_cors_allow_credentials,
                    allow_methods=list(settings.cors_allowed_methods_list),
                    allow_headers=list(settings.cors_allowed_headers_list),
                )
            else:
                self._cors_app = self.app

        return self._cors_app

    async def __call__(self, scope, receive, send):
        await self._current_app()(scope, receive, send)
