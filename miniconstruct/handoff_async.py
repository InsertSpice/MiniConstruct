"""Admit local materialization before using Starlette's shared AnyIO pool."""
import asyncio
import logging
from threading import Lock

from anyio import CapacityLimiter, to_thread
from anyio.lowlevel import RunVar

from miniconstruct.handoff_media import WORKER_CAPACITY

# Admission is per server event loop, like AnyIO's default thread limiter.
_admission = RunVar("visual_handoff_admission")
# Only admitted pool tasks are retained; queued HTTP owners remain cancellable.
_materializations = set()
_log = logging.getLogger(__name__)


def _admission_limiter():
    try:
        return _admission.get()
    except LookupError:
        limiter = CapacityLimiter(WORKER_CAPACITY)
        _admission.set(limiter)
        return limiter


async def materialize_off_loop(operation):
    limiter = _admission_limiter()
    borrower = asyncio.current_task()
    await limiter.acquire()  # No pool task/thread exists while awaiting admission.
    owner_cancelled = started = False
    state_lock = Lock()

    def run():
        nonlocal started
        # Cancellation may race with a dispatched thread's first instruction.
        # Claim running ownership atomically, or skip the cancelled operation.
        with state_lock:
            if owner_cancelled:
                return None
            started = True
        return operation()

    task = asyncio.create_task(to_thread.run_sync(run))
    _materializations.add(task)

    def completed(done):
        _materializations.discard(done)
        limiter.release_on_behalf_of(borrower)
        if not done.cancelled():
            error = done.exception()
            if owner_cancelled and error is not None:
                _log.warning("Visual Handoff materialization failed after request cancellation",
                             exc_info=(type(error), error, error.__traceback__))

    task.add_done_callback(completed)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with state_lock:
            owner_cancelled = True
            if not started:
                # Still waiting for a shared-pool token (or dispatch). The guard
                # above also prevents work in an already-dispatched thread.
                task.cancel()
        # A started worker keeps BOTH tokens until actual completion. Native task
        # cancellation must not abandon its thread and free those tokens early.
        raise
