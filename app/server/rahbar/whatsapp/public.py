"""Allow only the signed webhook through a development tunnel."""

from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send


class WebhookOnly:
    def __init__(self, application: ASGIApp) -> None:
        self.application = application

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.application(scope, receive, send)
        elif (
            scope["type"] == "http"
            and scope["path"] == "/webhooks/whatsapp"
            and scope["method"] in {"GET", "POST"}
        ):
            await self.application(scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await Response(status_code=404)(scope, receive, send)
