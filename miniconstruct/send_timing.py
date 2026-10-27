"""Opt-in, bounded Send stage timings without prompts, identities or paths."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import logging
import os
from time import perf_counter

_active = ContextVar("send_timing", default=None)
logger = logging.getLogger("uvicorn.error")


@contextmanager
def stage(name):
    values = _active.get()
    if values is None:
        yield
        return
    started = perf_counter()
    try:
        yield
    finally:
        values["stagesMs"][name] = round(values["stagesMs"].get(name, 0) + (perf_counter() - started) * 1000, 3)


def count(name, amount=1):
    values = _active.get()
    if values is not None:
        values["counts"][name] = values["counts"].get(name, 0) + amount


def profile_send(function):
    @wraps(function)
    def profiled(*args, **kwargs):
        if os.environ.get("MINICONSTRUCT_SEND_TIMING") != "1":
            return function(*args, **kwargs)
        values = {"stagesMs": {}, "counts": {}}
        token = _active.set(values)
        started = perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            values["totalMs"] = round((perf_counter() - started) * 1000, 3)
            _active.reset(token)
            logger.info("Send timing %s", json.dumps(values, separators=(",", ":")))
    return profiled
