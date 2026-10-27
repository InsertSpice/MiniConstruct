"""Capture structured service calls without replacing production assembly/parsing."""
import time
import inspect
from typing import Any

from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.llm.compatibility import build_generation_payload


class _SingleAttemptError(Exception):
    def __init__(self, error):
        self.error = error


class SingleAttemptClient(OpenAICompatibleClient):
    """Benchmark-only transport policy; production compatibility policy is unchanged.

    Escape the production compatibility retry loop on its first transport error,
    then restore the normal error type at the service boundary. Always submit the
    canonical seed, irrespective of the interactive client's compatibility cache.
    """

    async def _request(self, *args, **kwargs):
        try:
            return await super()._request(*args, **kwargs)
        except LLMBackendError as exc:
            raise _SingleAttemptError(exc) from exc

    async def generate(self, messages, *, response_instruction=None, response_format=None):
        self._seed_known_unsupported = False
        self.seed_known_unsupported = False
        self.effective_seed = self.settings.seed
        try:
            return await super().generate(messages, response_instruction=response_instruction,
                                          response_format=response_format)
        except _SingleAttemptError as exc:
            raise exc.error from exc


class RecordingClient:
    def __init__(self, wrapped: Any, raw_outputs: list[str], settings=None) -> None:
        self.wrapped = wrapped
        self.raw_outputs = raw_outputs
        self.settings = settings
        self.payload = None
        self.last_request_duration_ms = None

    async def __aenter__(self):
        await self.wrapped.__aenter__()
        return self

    async def __aexit__(self, *args):
        await self.wrapped.__aexit__(*args)

    def _finalize_request_capture(self, started):
        # The wrapped production client replaces this after compatibility
        # fallback; preserve the actual final request, not the first try.
        self.payload = getattr(self.wrapped, "last_payload", None) or self.payload
        if self.payload is not None and (getattr(self.wrapped, "seed_unsupported", False) or
                                         getattr(self.wrapped, "seed_known_unsupported", False)):
            self.payload.pop("seed", None)
        self.last_request_duration_ms = round((time.perf_counter() - started) * 1000, 2)

    async def _generate_wrapped(self, method, messages, *, response_instruction, response_format):
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            parameters = {}
        kwargs = {"response_instruction": response_instruction}
        if "response_format" in parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
            kwargs["response_format"] = response_format
        return await method(messages, **kwargs)

    async def generate(self, messages, *, response_instruction=None, response_format=None):
        if self.settings is not None:
            self.payload, _ = build_generation_payload(
                self.settings, messages, stream=False, response_instruction=response_instruction,
                response_format=response_format,
            )
        started = time.perf_counter()
        try:
            raw = await self._generate_wrapped(self.wrapped.generate, messages,
                                               response_instruction=response_instruction,
                                               response_format=response_format)
        finally:
            self._finalize_request_capture(started)
        self.raw_outputs.append(raw)
        return raw

    async def generate_streamed(self, messages, *, response_instruction=None, response_format=None):
        if self.settings is not None:
            self.payload, _ = build_generation_payload(
                self.settings, messages, stream=True, response_instruction=response_instruction,
                response_format=response_format,
            )
        started = time.perf_counter()
        try:
            generate = getattr(self.wrapped, "generate_streamed", None)
            raw = await self._generate_wrapped(
                generate or self.wrapped.generate, messages,
                response_instruction=response_instruction, response_format=response_format,
            )
        finally:
            self._finalize_request_capture(started)
        self.raw_outputs.append(raw)
        return raw

    def __getattr__(self, name):
        return getattr(self.wrapped, name)


class RecordingCaptureError(ValueError):
    """The benchmark cannot establish exactly one actual captured request."""


def require_single_recorded_request(recorders: list[RecordingClient]) -> RecordingClient:
    populated = [recorder for recorder in recorders if recorder.payload is not None]
    if not populated:
        raise RecordingCaptureError(
            "Story benchmark instrumentation captured zero provider request payloads; "
            "client construction alone does not count",
        )
    if len(populated) != 1:
        raise RecordingCaptureError(
            f"Story benchmark instrumentation captured {len(populated)} provider request payloads; "
            "expected exactly one",
        )
    return populated[0]
