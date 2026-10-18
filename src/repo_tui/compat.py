"""Backports for the oldest Python repo-tui supports (3.8, Ubuntu 20.04)."""

from __future__ import annotations

import asyncio
import contextvars
import functools


async def to_thread(func, /, *args, **kwargs):
    """asyncio.to_thread (3.9+): run `func` in the default executor, with
    the caller's context variables, and await its result."""
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    call = functools.partial(ctx.run, func, *args, **kwargs)
    return await loop.run_in_executor(None, call)
