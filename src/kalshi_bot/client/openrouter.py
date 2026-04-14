"""OpenRouter LLM client for market analysis."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Ordered fallback chain: primary -> fallback models
DEFAULT_FALLBACK_MODELS = [
    "deepseek/deepseek-chat",
    "qwen/qwen3-max-thinking",
]


class OpenRouterClient:
    """Async client for OpenRouter chat completions with model fallback."""

    def __init__(
        self,
        api_key: str,
        model: str = "minimax/minimax-m2.7",
        fallback_models: list[str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._fallback_models = fallback_models or DEFAULT_FALLBACK_MODELS
        self._client = httpx.AsyncClient(timeout=45.0)
        self.last_model_used: str = ""

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        """Send a chat completion request with automatic model fallback.

        Tries the primary model first, then each fallback on 429/5xx errors.
        Returns empty string only if all models fail.
        """
        models_to_try = [self._model, *self._fallback_models]

        for model in models_to_try:
            result = await self._try_model(model, system_prompt, user_prompt)
            if result is not None:
                self.last_model_used = model
                return result

        logger.error("All models exhausted — no AI analysis available")
        self.last_model_used = ""
        return ""

    async def _try_model(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str | None:
        """Try a single model. Returns text on success, None on retryable failure."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 150,
            "temperature": 0.3,
        }
        try:
            resp = await self._client.post(
                OPENROUTER_API_URL,
                headers=headers,
                json=payload,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                logger.warning(
                    "OpenRouter %s for %s (status=%s) — trying fallback",
                    "rate-limited" if resp.status_code == 429 else "server error",
                    model,
                    resp.status_code,
                )
                return None
            if resp.status_code != 200:
                logger.error(
                    "OpenRouter API error: status=%s model=%s response=%s",
                    resp.status_code,
                    model,
                    resp.text[:200],
                )
                return ""
            data = resp.json()
            if "choices" not in data or not data["choices"]:
                logger.error("OpenRouter response missing choices: %s", data)
                return ""
            text = str(data["choices"][0]["message"]["content"]).strip()
            # Strip <think> tags if present
            if "<think>" in text and "</think>" in text:
                text = text.split("</think>")[-1].strip()
            # Strip ``` tags if present
            if text.startswith("```") and text.endswith("```"):
                text = text[3:-3].strip()
            if text:
                logger.info("OpenRouter response from %s (%d chars)", model, len(text))
            return text
        except Exception:
            logger.exception("OpenRouter request failed for model %s", model)
            return None

    async def close(self) -> None:
        await self._client.aclose()
