"""Minimal Ollama HTTP client (local inference only).

Every failure mode (service down, model missing, timeout, malformed reply) surfaces as
LLMUnavailable so callers can fall back to deterministic output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx


class LLMUnavailable(RuntimeError):
    """The local language model cannot answer right now."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass
class ChatReply:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    eval_ms: int | None = None


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def list_models(self) -> list[str]:
        try:
            r = self._client.get("/api/tags", timeout=2.0)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable("service_unreachable", type(exc).__name__) from exc
        return [m.get("name", "") for m in r.json().get("models", [])]

    def status(self, model: str) -> dict[str, Any]:
        try:
            models = self.list_models()
        except LLMUnavailable as exc:
            return {"reachable": False, "model": model, "model_installed": False, "reason": exc.reason}
        installed = _model_installed(model, models)
        return {
            "reachable": True,
            "model": model,
            "model_installed": installed,
            "installed_models": models,
            "reason": None if installed else "model_not_installed",
        }

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        return _embed_impl(self._client, model, texts)

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.1,
        num_ctx: int = 8192,
        format: dict[str, Any] | str | None = None,
    ) -> ChatReply:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": num_ctx},
        }
        if tools:
            payload["tools"] = tools
        if format is not None:
            payload["format"] = format
        try:
            r = self._client.post("/api/chat", json=payload)
        except httpx.TimeoutException as exc:  # the service answered too slowly: busy, not absent
            raise LLMUnavailable("model_timeout", type(exc).__name__) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable("service_unreachable", type(exc).__name__) from exc
        if r.status_code == 404:
            raise LLMUnavailable("model_not_installed", model)
        if r.status_code >= 400:
            raise LLMUnavailable("model_error", r.text[:300])
        try:
            body = r.json()
            msg = body["message"]
        except (ValueError, KeyError) as exc:
            raise LLMUnavailable("malformed_reply", str(exc)) from exc
        calls = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):  # some models return JSON text
                import json

                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            calls.append({"name": fn.get("name", ""), "arguments": args})
        eval_ns = body.get("total_duration")
        return ChatReply(
            content=msg.get("content") or "",
            tool_calls=calls,
            model=body.get("model", model),
            eval_ms=int(eval_ns / 1e6) if isinstance(eval_ns, (int, float)) else None,
        )


def _embed_impl(client: httpx.Client, model: str, texts: list[str]) -> list[list[float]]:
    try:
        r = client.post("/api/embed", json={"model": model, "input": texts})
    except httpx.HTTPError as exc:
        raise LLMUnavailable("service_unreachable", type(exc).__name__) from exc
    if r.status_code == 404:
        raise LLMUnavailable("model_not_installed", model)
    if r.status_code >= 400:
        raise LLMUnavailable("model_error", r.text[:300])
    try:
        vecs = r.json()["embeddings"]
    except (ValueError, KeyError) as exc:
        raise LLMUnavailable("malformed_reply", str(exc)) from exc
    if len(vecs) != len(texts):
        raise LLMUnavailable("malformed_reply", "embedding count mismatch")
    return vecs


def _model_installed(model: str, installed: list[str]) -> bool:
    want = model if ":" in model else f"{model}:latest"
    return any(m == model or m == want for m in installed)
