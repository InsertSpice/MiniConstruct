"""Capture structured service calls without replacing production assembly/parsing."""
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

    async def generate(self, messages, *, response_instruction=None):
        self._seed_known_unsupported = False
        self.seed_known_unsupported = False
        self.effective_seed = self.settings.seed
        try:
            return await super().generate(messages, response_instruction=response_instruction)
        except _SingleAttemptError as exc:
            raise exc.error from exc


class RecordingClient:
    def __init__(self, wrapped: Any, raw_outputs: list[str], settings=None) -> None:
        self.wrapped = wrapped
        self.raw_outputs = raw_outputs
        self.settings = settings
        self.payload = None

    async def __aenter__(self):
        await self.wrapped.__aenter__()
        return self

    async def __aexit__(self, *args):
        await self.wrapped.__aexit__(*args)

    async def generate(self, messages, *, response_instruction=None):
        if self.settings is not None:
            self.payload, _ = build_generation_payload(
                self.settings, messages, stream=False, response_instruction=response_instruction,
            )
        raw = await self.wrapped.generate(messages, response_instruction=response_instruction)
        self.raw_outputs.append(raw)
        return raw

    def __getattr__(self, name):
        return getattr(self.wrapped, name)
