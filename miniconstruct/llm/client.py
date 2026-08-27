from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import json
import re
from typing import Any

import httpx

from miniconstruct.models.api import LLMSettings
from miniconstruct.llm.compatibility import build_generation_payload
from miniconstruct.llm.diagnostics import safe_usage


class LLMBackendError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, upstream_status: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.upstream_status = upstream_status


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
            raise LLMBackendError("The LLM endpoint timed out.", 504) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise LLMBackendError(
                f"LLM endpoint returned HTTP {exc.response.status_code}: {detail}",
                502,
                upstream_status=exc.response.status_code,
            ) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise LLMBackendError(f"Could not use the LLM endpoint: {exc}", 502) from exc

    async def list_models(self) -> list[str]:
        data = await self._request("GET", "models")
        items = data.get("data")
        if not isinstance(items, list):
            raise LLMBackendError("Model-list response does not contain a data array.")
        return sorted({item["id"] for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)})

    async def test_connection(self) -> dict[str, Any]:
        models = await self.list_models()
        return {"ok": True, "models": models, "message": f"Connected; discovered {len(models)} model(s)."}

    async def generate(self, messages: list[dict[str, Any]]) -> str:
        if not self.settings.selected_model_id.strip():
            raise LLMBackendError("A model ID is required.", 400)
        payload, optional = build_generation_payload(self.settings, messages, stream=False)
        try:
            data = await self._request("POST", "chat/completions", json=payload)
        except LLMBackendError as exc:
            if exc.upstream_status not in {400, 422} or not optional:
                raise
            payload, _ = build_generation_payload(
                self.settings, messages, stream=False, include_optional=False
            )
            data = await self._request("POST", "chat/completions", json=payload)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMBackendError("Malformed chat-completions response: missing choices[0].message.content.") from exc
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text = "".join(
                part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
            ).strip()
            if text:
                return text
        raise LLMBackendError("Malformed chat-completions response: content is not text.")

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

    async def stream_events(self, messages: list[dict[str, Any]]) -> AsyncIterator[LLMStreamEvent]:
        if not self.settings.selected_model_id.strip():
            raise LLMBackendError("A model ID is required.", 400)
        timeout = httpx.Timeout(connect=10.0, write=30.0, read=None, pool=10.0)
        try:
            payload, optional = build_generation_payload(self.settings, messages, stream=True)
            for attempt in range(2):
                splitter = ReasoningSplitter()
                async with self.client.stream(
                    "POST", "chat/completions", json=payload, timeout=timeout,
                ) as response:
                    if response.is_error:
                        detail = (await response.aread()).decode(errors="replace")[:500]
                        if attempt == 0 and response.status_code in {400, 422} and optional:
                            payload, _ = build_generation_payload(
                                self.settings, messages, stream=True, include_optional=False
                            )
                            yield LLMStreamEvent("compatibility_fallback")
                            continue
                        raise LLMBackendError(
                            f"LLM endpoint returned HTTP {response.status_code}: {detail}",
                            502,
                            upstream_status=response.status_code,
                        )
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
            raise LLMBackendError("The LLM endpoint timed out while connecting or writing the request.", 504) from exc
        except httpx.RequestError as exc:
            raise LLMBackendError(f"The LLM stream ended unexpectedly: {exc}", 502) from exc


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
