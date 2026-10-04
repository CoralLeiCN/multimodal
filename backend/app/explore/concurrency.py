"""Keep blocking transactions off the event loop and finish them before cleanup."""

import asyncio

import anyio


async def complete(awaitable):
    # A worker thread cannot be cancelled. Wait for its commit/rollback before
    # cancellation starts a competing transaction or closes its connection.
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    # Starlette also uses level-triggered AnyIO cancellation on disconnect.
    # Shield that scope to avoid repeatedly cancelling this wait while I/O ends.
    with anyio.CancelScope(shield=True):
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancelled = True
            except Exception:
                if cancelled:
                    raise asyncio.CancelledError from None
                raise
    if cancelled:
        raise asyncio.CancelledError
    return result


async def blocking(function, *args, **kwargs):
    return await complete(asyncio.to_thread(function, *args, **kwargs))
