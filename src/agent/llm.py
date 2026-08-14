"""SpaceXAI (xAI) LLM client — OpenAI-compatible API."""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from src.config import get_settings
from src.logging_setup import setup_logging

logger = setup_logging("agent.llm")


class LLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: OpenAI | None = None
        self.token_usage = 0

    @property
    def available(self) -> bool:
        return self.settings.has_llm

    def _get_client(self) -> OpenAI:
        if self._client is None:
            if not self.available:
                raise RuntimeError(
                    "XAI_API_KEY is not set. Add it to .env to enable LLM features."
                )
            self._client = OpenAI(
                api_key=self.settings.xai_api_key,
                base_url=self.settings.llm_base_url,
                timeout=self.settings.response_timeout_sec,
            )
        return self._client

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> str:
        client = self._get_client()
        try:
            # Prefer Responses API; fall back to chat.completions
            resp = client.responses.create(
                model=self.settings.llm_model,
                input=messages,
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            text = getattr(resp, "output_text", None) or ""
            usage = getattr(resp, "usage", None)
            if usage is not None:
                total = getattr(usage, "total_tokens", None) or 0
                self.token_usage += int(total or 0)
            if text:
                return text.strip()
        except Exception as exc:
            logger.warning("Responses API failed (%s); trying chat.completions", exc)

        resp = client.chat.completions.create(
            model=self.settings.llm_model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if resp.usage:
            self.token_usage += int(resp.usage.total_tokens or 0)
        content = resp.choices[0].message.content or ""
        return content.strip()

    def extract_json(self, text: str) -> dict[str, Any] | list[Any] | None:
        text = text.strip()
        # Strip markdown fences
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence:
            text = fence.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find first { ... } or [ ... ]
            for start, end in (("{", "}"), ("[", "]")):
                i = text.find(start)
                j = text.rfind(end)
                if i >= 0 and j > i:
                    try:
                        return json.loads(text[i : j + 1])
                    except json.JSONDecodeError:
                        continue
        return None


# Shared singleton for token tracking in a session
_llm: LLMClient | None = None


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm
