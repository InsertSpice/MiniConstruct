"""Own an operation task until it completes or its HTTP request disconnects."""
import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

from starlette.requests import Request

T = TypeVar("T")


async def run_until_disconnect(http_request: Request, operation: Coroutine[Any, Any, T]) -> T:
    """Cancel and await the exact operation when its request disconnects."""
    async def disconnected() -> None:
        # The request body has already been consumed by FastAPI.
        while True:
            message = await http_request.receive()
            if message["type"] == "http.disconnect":
                return

    operation_task = asyncio.create_task(operation)
    disconnect_task = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait(
            (operation_task, disconnect_task), return_when=asyncio.FIRST_COMPLETED,
        )
        if operation_task in done:
            return await operation_task
        await disconnect_task  # Propagate receive errors too.
        raise asyncio.CancelledError("HTTP request disconnected")
    finally:
        for task in (operation_task, disconnect_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(operation_task, disconnect_task, return_exceptions=True)
