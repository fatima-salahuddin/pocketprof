"""OpenAI-compatible arm of the LLMClient interface: OpenRouter, Gemini's OpenAI endpoint, local Ollama.

The Ollama arm keeps real student data on institutional hardware (C-5). On OpenRouter, `models` makes
the router fall through to the next model when one is rate-limited (free models often are); it accepts
at most three models in total.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator

from openai import OpenAI

from .llm import ChatTurn


def is_local(base_url: str) -> bool:
    return any(h in base_url for h in ("localhost", "127.0.0.1", ":11434", "ollama:"))


class OpenAICompatClient:
    def __init__(self, model: str, base_url: str, api_key: str, timeout_s: float, temperature: float,
                 seed: int, fallbacks: list[str]):
        self.model, self.base_url = model, base_url or None
        self.temperature, self.seed, self.fallbacks = temperature, seed, fallbacks
        self.local = is_local(base_url or "")
        self.api_key = api_key or ("ollama" if self.local else "")
        self.timeout_s = timeout_s if not self.local else max(timeout_s, 600)
        where = "local Ollama" if self.local else (base_url or "OpenAI")
        self.name = f"{model} via {where}"

    def stream(self, system: str, messages: list[ChatTurn], *, max_tokens: int,
               stop: threading.Event) -> Iterator[str]:
        # One client per request: closing it is how a stop hangs up mid-generation, which makes
        # Ollama stop generating instead of finishing an answer nobody will read.
        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout_s,
                        max_retries=2 if self.local else 4)
        finished = threading.Event()

        def hang_up_on_stop():
            while not finished.is_set():
                if stop.wait(0.3):
                    client.close()
                    return

        threading.Thread(target=hang_up_on_stop, daemon=True).start()
        extra: dict = {}
        if "openrouter.ai" in (self.base_url or ""):
            extra["reasoning"] = {"effort": "low", "exclude": True}
            if self.fallbacks:
                extra["models"] = ([self.model] + self.fallbacks)[:3]
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}] + [{"role": m.role, "content": m.content}
                                                                  for m in messages],
                temperature=self.temperature, seed=self.seed, max_tokens=max_tokens, stream=True,
                extra_body=extra or None)
            for chunk in resp:
                if stop.is_set():
                    break
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        finally:
            finished.set()
            client.close()
