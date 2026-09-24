"""Thin Ollama client tuned for small models (Qwen3 4B).

Small models are unreliable at free-form output, so every structured call uses
Ollama's JSON-schema `format` constraint, and callers keep prompts short.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

import httpx

from .config import LLMConfig

log = logging.getLogger(__name__)
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    def chat(self, system: str, user: str, *, max_tokens: int = 512, temperature: float | None = None) -> str: ...

    def json(self, system: str, user: str, schema: dict, *, max_tokens: int = 512) -> dict[str, Any]: ...


class OllamaLLM:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self._http = httpx.Client(base_url=cfg.base_url, timeout=cfg.timeout_s)
        self._think_supported = True

    # ------------------------------------------------------------ health ---
    def check(self) -> None:
        try:
            tags = self._http.get("/api/tags").json()
        except httpx.HTTPError as exc:
            raise LLMError(f"Cannot reach Ollama at {self.cfg.base_url}: {exc}. Is `ollama serve` running?") from exc
        names = {m["name"] for m in tags.get("models", [])}
        wanted = self.cfg.model
        if wanted not in names and f"{wanted}:latest" not in names:
            raise LLMError(f"Model {wanted!r} not pulled. Run: ollama pull {wanted}   (have: {sorted(names)})")

    # -------------------------------------------------------------- core ---
    def _post(self, payload: dict) -> str:
        if self._think_supported:
            payload["think"] = self.cfg.think
        try:
            resp = self._http.post("/api/chat", json=payload)
            if resp.status_code == 400 and "think" in resp.text.lower():
                # older Ollama / non-reasoning model (e.g. qwen:4b): retry without the flag
                self._think_supported = False
                payload.pop("think", None)
                resp = self._http.post("/api/chat", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc
        content = resp.json().get("message", {}).get("content", "")
        return _THINK_RE.sub("", content).strip()

    def _payload(self, system: str, user: str, max_tokens: int, temperature: float | None, schema: dict | None) -> dict:
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {
                "temperature": self.cfg.temperature if temperature is None else temperature,
                "num_ctx": self.cfg.num_ctx,
                "num_predict": max_tokens,
            },
        }
        if schema is not None:
            payload["format"] = schema
        return payload

    # --------------------------------------------------------- public API ---
    def chat(self, system: str, user: str, *, max_tokens: int = 512, temperature: float | None = None) -> str:
        return self._post(self._payload(system, user, max_tokens, temperature, None))

    def json(self, system: str, user: str, schema: dict, *, max_tokens: int = 512) -> dict[str, Any]:
        last_err: Exception | None = None
        for attempt in range(2):
            raw = self._post(self._payload(system, user, max_tokens, 0.0 if attempt else None, schema))
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
                last_err = LLMError(f"expected JSON object, got {type(data).__name__}")
            except json.JSONDecodeError as exc:
                last_err = exc
            log.warning("LLM returned invalid JSON (attempt %d): %r", attempt + 1, raw[:200])
        raise LLMError(f"LLM failed to produce valid JSON: {last_err}")
