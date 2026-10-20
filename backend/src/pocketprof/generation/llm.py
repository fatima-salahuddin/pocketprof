"""LLMClient interface (SDS 3.2, Table 2 "provider-agnostic LLMClient; hosted and Ollama arms").

Every generation mode talks to this interface only, so an experiment swaps a model by configuration.
Arms:
    anthropic  Claude through the official SDK (anthropic_client.py)
    openai     any OpenAI-compatible endpoint: OpenRouter, Gemini, local Ollama (openai_compat.py)
    none       no model: the responder falls back to showing the retrieved passages (fail closed)
"""
from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from ..arms import Generation
from ..config import get_settings


@dataclass
class ChatTurn:
    role: str      # user | assistant
    content: str


class GenerationRefused(Exception):
    """The model provider declined the request (safety stop), as opposed to failing."""


class LLMClient(Protocol):
    name: str

    def stream(self, system: str, messages: list[ChatTurn], *, max_tokens: int,
               stop: threading.Event) -> Iterator[str]:
        """Yield answer text as it is generated. Setting `stop` must end generation promptly."""
        ...


def provider(gen: Generation) -> str:
    p = gen.provider if gen.provider != "auto" else get_settings().gen_provider
    if p != "auto":
        return p
    s = get_settings()
    if s.gen_base_url:
        return "openai"
    if os.getenv("ANTHROPIC_API_KEY") or (s.gen_api_key and not s.gen_base_url):
        return "anthropic"
    return "none"


_clients: dict[tuple, LLMClient | None] = {}
_lock = threading.Lock()


def get_client(gen: Generation) -> LLMClient | None:
    s = get_settings()
    p = provider(gen)
    model = gen.model or s.gen_model
    key = (p, model, s.gen_base_url)
    with _lock:
        if key not in _clients:
            if p == "anthropic":
                from .anthropic_client import AnthropicClient
                _clients[key] = AnthropicClient(model or "claude-opus-5-5", s.gen_api_key or None, s.gen_timeout_s)
            elif p == "openai":
                from .openai_compat import OpenAICompatClient
                fallbacks = [m.strip() for m in s.gen_fallback_models.split(",") if m.strip()]
                _clients[key] = OpenAICompatClient(model or "gpt-4o-mini", s.gen_base_url, s.gen_api_key,
                                                   s.gen_timeout_s, gen.temperature, gen.seed, fallbacks)
            else:
                _clients[key] = None
        return _clients[key]


def describe(gen: Generation) -> str:
    client = get_client(gen)
    return client.name if client else "none (passages only)"
