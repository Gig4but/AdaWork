"""Talks to llama-server's OpenAI-compatible chat endpoint (also works with Ollama).

Answers are constrained by a JSON schema, so the reply always parses; the caller still
validates the values. With the bundled llama-server the schema is sent as a compact grammar
(no whitespace, fewer tokens); other servers get it as `response_format`. Each call reports
its token counts and times, which feed the speed statistics (PLAN §3.2.1).
"""

import base64
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from adawork.ai.grammar import compact_grammar


class LlmError(Exception):
    pass


class BadAnswer(LlmError):
    """The server answered, but not with something usable for this input."""


class ServerDown(LlmError):
    """Nothing answers at all (not running, crashed): no use trying the next input."""


@dataclass(frozen=True)
class Timing:
    prompt_tokens: int
    prompt_seconds: float
    output_tokens: int
    output_seconds: float
    wall_seconds: float

    @property
    def prompt_tps(self) -> float:
        return self.prompt_tokens / self.prompt_seconds if self.prompt_seconds else 0.0

    @property
    def output_tps(self) -> float:
        return self.output_tokens / self.output_seconds if self.output_seconds else 0.0

    @staticmethod
    def total(timings: "list[Timing]") -> "Timing":
        return Timing(
            sum(t.prompt_tokens for t in timings),
            sum(t.prompt_seconds for t in timings),
            sum(t.output_tokens for t in timings),
            sum(t.output_seconds for t in timings),
            sum(t.wall_seconds for t in timings),
        )


@dataclass(frozen=True)
class Reply:
    data: Any  # parsed JSON (or text when no schema was given)
    timing: Timing


def image_part(data: bytes, mime: str = "image/jpeg") -> dict:
    url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    return {"type": "image_url", "image_url": {"url": url}}


class LlmClient:
    def __init__(
        self,
        base_url: str,
        model: str = "local",
        chat_kwargs: dict | None = None,
        native: bool = True,
        timeout: float = 300,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.chat_kwargs = chat_kwargs
        self.native = native  # our llama-server (grammar support) vs. Ollama or another server
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def chat(
        self,
        messages: list[dict],
        schema: dict | None = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> Reply:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "cache_prompt": True,  # the shared instructions are processed only once
        }
        if schema is not None and self.native:
            body["grammar"] = compact_grammar(schema)
        elif schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": schema, "strict": True},
            }
        if self.chat_kwargs:
            body["chat_template_kwargs"] = self.chat_kwargs
        started = time.perf_counter()
        try:
            resp = self._client.post(f"{self.base_url}/v1/chat/completions", json=body)
        except httpx.TimeoutException as exc:  # this input may just take too long
            raise LlmError(f"AI server did not answer in time: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ServerDown(f"AI server unreachable: {exc}") from exc
        wall = time.perf_counter() - started
        if resp.status_code != 200:
            raise LlmError(f"AI server answered {resp.status_code}: {resp.text[:300]}")
        try:
            payload = resp.json()
            choice = payload["choices"][0]
            text = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"unexpected reply from the AI server: {resp.text[:200]}") from exc
        if choice.get("finish_reason") == "length":
            raise BadAnswer("answer was cut off (max_tokens)")
        data: Any = text
        if schema is not None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise BadAnswer(f"invalid JSON from the model: {text[:200]}") from exc
            if schema.get("type") == "object" and not isinstance(data, dict):
                raise BadAnswer(f"not a JSON object: {text[:200]}")  # a server without grammar
        return Reply(data, _timing(payload, wall))


def _timing(payload: dict, wall: float) -> Timing:
    t = payload.get("timings") or {}  # llama-server; Ollama only has usage
    usage = payload.get("usage") or {}
    return Timing(
        prompt_tokens=int(t.get("prompt_n", usage.get("prompt_tokens", 0))),
        prompt_seconds=float(t.get("prompt_ms", 0)) / 1000,
        output_tokens=int(t.get("predicted_n", usage.get("completion_tokens", 0))),
        output_seconds=float(t.get("predicted_ms", 0)) / 1000,
        wall_seconds=wall,
    )
