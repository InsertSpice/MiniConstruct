from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import json
import re
from typing import Any

import httpx

from miniconstruct.models.api import LLMSettings
from miniconstruct.llm.compatibility import DIRECT_OUTPUT_INSTRUCTION, build_generation_payload
from miniconstruct.llm.diagnostics import safe_usage


class LLMBackendError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, upstream_status: int | None = None,
                 *, category: str = "provider_error", safe_message: str | None = None,
                 raw_response: str | None = None, raw_response_visible: bool = True) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.upstream_status = upstream_status
        self.category = category
        self.safe_message = safe_message or (message if category.startswith("story_") else "The LLM request failed.")
        self.raw_response = raw_response
        self.raw_response_visible = raw_response_visible


@dataclass(slots=True)
class LLMStreamEvent:
    kind: str
    text: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class ReasoningSplitter:
    OPEN = re.compile(r"<(think|thinking|reasoning)>", re.I)
    CLOSE = re.compile(r"</(think|thinking|reasoning)>", re.I)

    def __init__(self) -> None:
        self.buffer = ""
        self.in_reasoning = False

    def feed(self, chunk: str) -> list[LLMStreamEvent]:
        self.buffer += chunk
        events: list[LLMStreamEvent] = []
        while self.buffer:
            pattern = self.CLOSE if self.in_reasoning else self.OPEN
            match = pattern.search(self.buffer)
            if match:
                head, self.buffer = self.buffer[:match.start()], self.buffer[match.end():]
                if head:
                    events.append(LLMStreamEvent("reasoning" if self.in_reasoning else "content", head))
                self.in_reasoning = not self.in_reasoning
                continue
            cut = self.buffer.rfind("<")
            if cut != -1 and len(self.buffer) - cut <= 12:
                head, tail = self.buffer[:cut], self.buffer[cut:]
            else:
                head, tail = self.buffer, ""
            if head:
                events.append(LLMStreamEvent("reasoning" if self.in_reasoning else "content", head))
            self.buffer = tail
            break
        return events

    def flush(self) -> list[LLMStreamEvent]:
        if not self.buffer:
            return []
        event = LLMStreamEvent("reasoning" if self.in_reasoning else "content", self.buffer)
        self.buffer = ""
        return [event]


class OpenAICompatibleClient:
    _unsupported_seed_endpoints: set[tuple[str, str]] = set()

    def __init__(self, settings: LLMSettings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        headers = {"Accept": "application/json"}
        if settings.endpoint.api_key and settings.endpoint.api_key.get_secret_value():
            headers["Authorization"] = f"Bearer {settings.endpoint.api_key.get_secret_value()}"
        self.client = httpx.AsyncClient(
            base_url=settings.endpoint.base_url + "/",
            headers=headers,
            timeout=settings.timeout_seconds,
            transport=transport,
        )
        self._seed_endpoint_key = (settings.endpoint.base_url, settings.selected_model_id)
        self._seed_known_unsupported = self._seed_endpoint_key in self._unsupported_seed_endpoints
        self.seed_known_unsupported = self._seed_known_unsupported
        self.seed_unsupported = False
        self.effective_seed: int | None = None if self._seed_known_unsupported else settings.seed
        self.compatibility_fallback = False
        self.last_usage: dict[str, int] = {}
        self.last_payload: dict[str, Any] | None = None

    async def __aenter__(self) -> "OpenAICompatibleClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self.client.request(method, path, **kwargs)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("response is not a JSON object")
            return data
        except httpx.TimeoutException as exc:
            kind = type(exc).__name__
            message = f"The LLM endpoint timed out ({kind}; timeoutSeconds={self.settings.timeout_seconds:g})."
            raise LLMBackendError(message, 504, category="provider_timeout", safe_message=message) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise LLMBackendError(
                f"LLM endpoint returned HTTP {exc.response.status_code}: {detail}",
                502,
                upstream_status=exc.response.status_code,
                category="provider_http",
                safe_message=f"LLM endpoint returned HTTP {exc.response.status_code}.",
            ) from exc
        except httpx.RequestError as exc:
            raise LLMBackendError(f"Could not use the LLM endpoint: {exc}", 502,
                                  category="provider_connection",
                                  safe_message=f"Could not use the LLM endpoint ({type(exc).__name__}).") from exc
        except ValueError as exc:
            raise LLMBackendError(f"Could not use the LLM endpoint: {exc}", 502,
                                  category="provider_response",
                                  safe_message="LLM endpoint returned invalid or non-object JSON.") from exc

    async def list_models(self) -> list[str]:
        data = await self._request("GET", "models")
        items = data.get("data")
        if not isinstance(items, list):
            raise LLMBackendError("Model-list response does not contain a data array.")
        return sorted({item["id"] for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)})

    async def test_connection(self) -> dict[str, Any]:
        models = await self.list_models()
        return {"ok": True, "models": models, "message": f"Connected; discovered {len(models)} model(s)."}

    async def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        response_instruction: str | None = DIRECT_OUTPUT_INSTRUCTION,
        response_format: dict[str, Any] | None = None,
        allow_retries: bool = True,
    ) -> str:
        if not self.settings.selected_model_id.strip():
            raise LLMBackendError("A model ID is required.", 400, category="request",
                                  safe_message="A model ID is required.")
        include_seed = not self._seed_known_unsupported
        include_optional = True
        seed_fallback_used = False
        optional_fallback_used = False
        for _ in range(3):
            payload, optional = build_generation_payload(
                self.settings,
                messages,
                stream=False,
                include_optional=include_optional,
                include_seed=include_seed,
                response_instruction=response_instruction,
                response_format=response_format,
            )
            self.last_payload = payload
            try:
                data = await self._request("POST", "chat/completions", json=payload)
                break
            except LLMBackendError as exc:
                if allow_retries and not seed_fallback_used and _is_seed_rejection(exc, optional):
                    include_seed = False
                    seed_fallback_used = True
                    self.seed_unsupported = True
                    self.effective_seed = None
                    self._unsupported_seed_endpoints.add(self._seed_endpoint_key)
                    continue
                if allow_retries and not optional_fallback_used and _is_optional_compatibility_rejection(exc, optional):
                    include_optional = False
                    optional_fallback_used = True
                    self.compatibility_fallback = True
                    continue
                raise
        self.last_usage = safe_usage(data.get("usage"))
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMBackendError("Malformed chat-completions response: missing choices[0].message.content.",
                                  category="provider_response",
                                  safe_message="Malformed chat-completions response: missing choices[0].message.content.") from exc
        if isinstance(content, str):
            return content if response_format is not None else content.strip()
        if isinstance(content, list):
            text = "".join(
                part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
            )
            if text.strip() or response_format is not None:
                return text if response_format is not None else text.strip()
        raise LLMBackendError("Malformed chat-completions response: content is not text.",
                              category="provider_response",
                              safe_message="Malformed chat-completions response: content is not text.")

    async def stream_generate(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        """Yield only textual OpenAI-compatible SSE deltas.

        The stream deliberately has no read timeout: local models can have long
        prompt-processing and token-generation pauses. Closing this generator
        closes the upstream response, which is the portable cancellation
        mechanism supported by OpenAI-compatible servers.
        """
        async for event in self.stream_events(messages):
            if event.kind == "content":
                yield event.text

    async def generate_streamed(
        self,
        messages: list[dict[str, Any]],
        *,
        response_instruction: str | None = DIRECT_OUTPUT_INSTRUCTION,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        content: list[str] = []
        usage: dict[str, int] = {}
        async for event in self.stream_events(
            messages,
            response_instruction=response_instruction,
            response_format=response_format,
            timeout=self.settings.timeout_seconds,
        ):
            if event.kind == "content":
                content.append(event.text)
            elif event.kind == "usage" and event.usage:
                usage.update(event.usage)
        self.last_usage = safe_usage(usage)
        joined = "".join(content)
        result = joined if response_format is not None else joined.strip()
        if not result.strip() and response_format is None:
            raise LLMBackendError(
                "Malformed streamed chat-completions response: no final content.",
                category="provider_response",
                safe_message="Malformed streamed chat-completions response: no final content.",
            )
        return result

    async def stream_events(
        self,
        messages: list[dict[str, Any]],
        *,
        response_instruction: str | None = DIRECT_OUTPUT_INSTRUCTION,
        response_format: dict[str, Any] | None = None,
        timeout: httpx.Timeout | float | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        if not self.settings.selected_model_id.strip():
            raise LLMBackendError("A model ID is required.", 400)
        request_timeout: httpx.Timeout
        if timeout is None:
            request_timeout = httpx.Timeout(connect=10.0, write=30.0, read=None, pool=10.0)
        elif isinstance(timeout, httpx.Timeout):
            request_timeout = timeout
        else:
            request_timeout = httpx.Timeout(timeout)
        try:
            include_seed = not self._seed_known_unsupported
            include_optional = True
            seed_fallback_used = False
            optional_fallback_used = False
            for _ in range(3):
                payload, optional = build_generation_payload(
                    self.settings,
                    messages,
                    stream=True,
                    include_optional=include_optional,
                    include_seed=include_seed,
                    response_instruction=response_instruction,
                    response_format=response_format,
                )
                self.last_payload = payload
                splitter = ReasoningSplitter()
                async with self.client.stream(
                    "POST", "chat/completions", json=payload, timeout=request_timeout,
                ) as response:
                    if response.is_error:
                        detail = (await response.aread()).decode(errors="replace")[:500]
                        error = LLMBackendError(
                            f"LLM endpoint returned HTTP {response.status_code}: {detail}",
                            502,
                            upstream_status=response.status_code,
                            category="provider_http",
                            safe_message=f"LLM endpoint returned HTTP {response.status_code}.",
                        )
                        if not seed_fallback_used and _is_seed_rejection(error, optional):
                            include_seed = False
                            seed_fallback_used = True
                            self.seed_unsupported = True
                            self.effective_seed = None
                            self._unsupported_seed_endpoints.add(self._seed_endpoint_key)
                            yield LLMStreamEvent("seed_unsupported")
                            continue
                        if not optional_fallback_used and _is_optional_compatibility_rejection(error, optional):
                            include_optional = False
                            optional_fallback_used = True
                            self.compatibility_fallback = True
                            yield LLMStreamEvent("compatibility_fallback")
                            continue
                        raise error
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type == "application/json":
                        try:
                            data = json.loads((await response.aread()).decode(errors="replace"))
                        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                            raise LLMBackendError(
                                "Malformed JSON response from the LLM endpoint.",
                                category="provider_response",
                                safe_message="Malformed JSON response from the LLM endpoint.",
                            ) from exc
                        for event in _decode_json_response(data, splitter):
                            yield event
                        for event in splitter.flush():
                            yield event
                        return
                    data_lines: list[str] = []
                    async for line in response.aiter_lines():
                        if line == "":
                            events, done = _decode_sse_data(data_lines, splitter)
                            data_lines.clear()
                            for event in events:
                                yield event
                            if done:
                                for event in splitter.flush():
                                    yield event
                                return
                            continue
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                    if data_lines:
                        events, _ = _decode_sse_data(data_lines, splitter)
                        for event in events:
                            yield event
                    for event in splitter.flush():
                        yield event
                    return
        except LLMBackendError:
            raise
        except httpx.TimeoutException as exc:
            message = "The LLM endpoint timed out while connecting, reading, or writing the request."
            raise LLMBackendError(message, 504, category="provider_timeout", safe_message=message) from exc
        except httpx.RequestError as exc:
            message = "The LLM stream ended unexpectedly."
            raise LLMBackendError(message, 502, category="provider_connection", safe_message=message) from exc


def _is_seed_rejection(error: LLMBackendError, optional_fields: set[str]) -> bool:
    """Only retry a seed-bearing request when the backend identifies seed itself."""
    if "seed" not in optional_fields or error.upstream_status not in {400, 422}:
        return False
    text = str(error).lower()
    return "seed" in text and any(token in text for token in ("unknown", "unsupported", "unrecognized", "unexpected", "extra field", "invalid parameter"))


def _is_optional_compatibility_rejection(error: LLMBackendError, optional_fields: set[str]) -> bool:
    """Retry only when a 400/422 specifically names an optional compatibility field."""
    fields = optional_fields - {"seed"}
    if not fields or error.upstream_status not in {400, 422}:
        return False
    text = str(error).lower()
    recognized = ("unknown", "unsupported", "unrecognized", "unexpected", "extra field", "invalid parameter")
    if any(field.lower() in text for field in fields):
        return any(token in text for token in recognized)
    # Some Unsloth-compatible servers report its known thinking kwargs only as
    # "unknown field". Preserve that existing, narrowly scoped compatibility path.
    return "chat_template_kwargs" in fields and "unknown field" in text


def _decode_sse_data(
    data_lines: list[str], splitter: ReasoningSplitter
) -> tuple[list[LLMStreamEvent], bool]:
    if not data_lines:
        return [], False
    payload = "\n".join(data_lines).strip()
    if payload == "[DONE]":
        return [], True
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return [], False
    if not isinstance(data, dict):
        return [], False
    events: list[LLMStreamEvent] = []
    usage = safe_usage(data.get("usage"))
    if usage:
        events.append(LLMStreamEvent("usage", usage=usage))
    choices = data.get("choices")
    delta = choices[0].get("delta", {}) if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    if not isinstance(delta, dict):
        delta = {}
    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        events.append(LLMStreamEvent("reasoning", reasoning))
    content = delta.get("content")
    if isinstance(content, str) and content:
        events.extend(splitter.feed(content))
    if isinstance(content, list):
        text = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
        )
        if text:
            events.extend(splitter.feed(text))
    if not events:
        events.append(LLMStreamEvent("metadata"))
    return events, False


def _decode_json_response(data: Any, splitter: ReasoningSplitter) -> list[LLMStreamEvent]:
    """Decode a provider that returned one normal JSON completion to a stream."""
    if not isinstance(data, dict):
        raise LLMBackendError(
            "Malformed JSON response from the LLM endpoint.",
            category="provider_response",
            safe_message="Malformed JSON response from the LLM endpoint.",
        )
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise LLMBackendError(
            "Malformed chat-completions response: missing choices.",
            category="provider_response",
            safe_message="Malformed chat-completions response: missing choices.",
        )
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        message = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
    events: list[LLMStreamEvent] = []
    usage = safe_usage(data.get("usage"))
    if usage:
        events.append(LLMStreamEvent("usage", usage=usage))
    for source in (choice, message):
        reasoning = source.get("reasoning_content") or source.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            events.append(LLMStreamEvent("reasoning", reasoning))
    content = message.get("content")
    if isinstance(content, str) and content:
        events.extend(splitter.feed(content))
    elif isinstance(content, list):
        text = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
        )
        if text:
            events.extend(splitter.feed(text))
    if not events:
        events.append(LLMStreamEvent("metadata"))
    return events
