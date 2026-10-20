"""Claude arm of the LLMClient interface, through the official Anthropic SDK.

Claude Opus 5.5 always thinks adaptively; depth is set with output_config.effort (no temperature or
thinking budget on this model). Grounded course Q&A is a short, retrieval-bound task, so effort is "low".
Thinking tokens count against max_tokens, so the budget is generous and the answer length is governed
by the prompt instead. Server-side fallbacks re-run a request that a safety classifier declines.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Iterator

import anthropic

from .llm import ChatTurn, GenerationRefused

log = logging.getLogger("pocketprof.generation.anthropic")

MIN_MAX_TOKENS = 16000


class AnthropicClient:
    def __init__(self, model: str, api_key: str | None, timeout_s: float, effort: str = "low"):
        self.model = model
        self.effort = effort
        self.name = f"Claude ({model})"
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=3) if api_key \
            else anthropic.Anthropic(timeout=timeout_s, max_retries=3)

    def stream(self, system: str, messages: list[ChatTurn], *, max_tokens: int,
               stop: threading.Event) -> Iterator[str]:
        with self._client.beta.messages.stream(
            model=self.model,
            max_tokens=max(max_tokens, MIN_MAX_TOKENS),
            system=system,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            for text in stream.text_stream:
                if stop.is_set():
                    return  # leaving the context manager closes the connection
                yield text
            final = stream.get_final_message()
            if final.stop_reason == "refusal":
                raise GenerationRefused(getattr(final.stop_details, "category", None) or "refusal")
