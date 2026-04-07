from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from typing import Iterable

try:
    from anthropic import Anthropic
except Exception:  # pragma: no cover - optional dependency per provider
    Anthropic = None
try:
    from json_repair import repair_json
except Exception:  # pragma: no cover - optional fallback helper
    repair_json = None
try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency per provider
    OpenAI = None


class LLMClient(ABC):
    @abstractmethod
    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def generate_text(self, system_prompt: str, user_prompt: str) -> tuple[str, dict]:
        raise NotImplementedError


class EmbeddingClient(ABC):
    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


def estimate_tokens(text: str) -> int:
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:
        return max(1, len(text) // 4)


class OpenAILLMClient(LLMClient):
    def __init__(self, model: str, reasoning_effort: str | None = None) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        if OpenAI is None:
            raise RuntimeError("openai is not installed; OpenAILLMClient requires the openai package.")
        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        text, _ = self.generate_text(system_prompt, user_prompt)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            if repair_json is None:
                raise
            return json.loads(repair_json(text))

    def generate_text(self, system_prompt: str, user_prompt: str) -> tuple[str, dict]:
        request = {
            "model": self.model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self.reasoning_effort and self.reasoning_effort.lower() not in {"", "none"}:
            request["reasoning"] = {"effort": self.reasoning_effort}
        try:
            response = self.client.responses.create(**request)
        except Exception as exc:
            message = str(exc)
            if "reasoning.effort" not in message or "Unsupported parameter" not in message:
                raise
            request.pop("reasoning", None)
            response = self.client.responses.create(**request)
        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", 0) if response.usage else 0,
            "output_tokens": getattr(response.usage, "output_tokens", 0) if response.usage else 0,
            "total_tokens": getattr(response.usage, "total_tokens", 0) if response.usage else 0,
        }
        return response.output_text, usage


class AnthropicLLMClient(LLMClient):
    def __init__(self, model: str, reasoning_effort: str | None = None) -> None:
        self.model = model
        if Anthropic is None:
            raise RuntimeError("anthropic is not installed; AnthropicLLMClient requires the anthropic package.")
        self.client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        text, _ = self.generate_text(system_prompt, user_prompt)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            if repair_json is None:
                raise
            return json.loads(repair_json(text))

    def generate_text(self, system_prompt: str, user_prompt: str) -> tuple[str, dict]:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1200,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", 0),
            "output_tokens": getattr(response.usage, "output_tokens", 0),
            "total_tokens": getattr(response.usage, "input_tokens", 0)
            + getattr(response.usage, "output_tokens", 0),
        }
        return text, usage


class OpenAIEmbeddingClient(EmbeddingClient):
    def __init__(self, model: str) -> None:
        self.model = model
        if OpenAI is None:
            raise RuntimeError("openai is not installed; OpenAIEmbeddingClient requires the openai package.")
        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in response.data]


class SentenceTransformerEmbeddingClient(EmbeddingClient):
    def __init__(self, model: str) -> None:
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()


def build_llm_client(provider: str, model: str, reasoning_effort: str | None = None) -> LLMClient:
    normalized = provider.lower()
    if normalized == "openai":
        return OpenAILLMClient(model=model, reasoning_effort=reasoning_effort)
    if normalized == "anthropic":
        return AnthropicLLMClient(model=model, reasoning_effort=reasoning_effort)
    raise ValueError(f"Unsupported LLM provider: {provider}")


def build_embedding_client(provider: str, model: str) -> EmbeddingClient:
    normalized = provider.lower()
    if normalized == "openai":
        return OpenAIEmbeddingClient(model=model)
    if normalized in {"sentence-transformers", "local"}:
        return SentenceTransformerEmbeddingClient(model=model)
    raise ValueError(f"Unsupported embedding provider: {provider}")


def batched(items: Iterable[str], size: int) -> Iterable[list[str]]:
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def with_retries(func, attempts: int = 5, initial_delay: float = 2.0):
    delay = initial_delay
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(delay)
            delay *= 2
