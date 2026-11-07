"""Quiet CLI HTTP diagnostics without changing application exception handling."""
from __future__ import annotations

import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send


logger = logging.getLogger("uvicorn.error")


class HTTPErrorLogging:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status = 0

        async def capture_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        # Log only after normal completion. Unhandled exceptions (including those
        # after response start) propagate to Uvicorn for a single traceback.
        await self.app(scope, receive, capture_status)
        if status >= 400:
            # Never include query strings, headers, bodies, or exception details.
            # repr also escapes newlines/control characters supplied in a path.
            logger.log(
                logging.ERROR if status >= 500 else logging.WARNING,
                "HTTP %s %r -> %d", scope["method"], scope["path"], status,
            )


def quiet_app() -> HTTPErrorLogging:
    # A factory import string keeps the same configuration in reload workers.
    from miniconstruct.app import app

    return HTTPErrorLogging(app)
