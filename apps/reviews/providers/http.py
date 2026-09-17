"""HTTP provider implementations."""

from time import monotonic

import requests

from apps.credentials.services import CredentialEncryption
from apps.reviews.providers.base import BaseAIProvider, ProviderError, ReviewRequest, ReviewResponse


class OpenAICompatibleProvider(BaseAIProvider):
    """Provider for OpenAI-compatible chat completion APIs."""

    def review(self, request: ReviewRequest) -> ReviewResponse:
        """Call the OpenAI-compatible chat completion endpoint."""
        started = monotonic()
        base = self.config.api_base_url.rstrip("/") if self.config.api_base_url else "https://api.openai.com/v1"
        headers = {"Content-Type": "application/json"}
        if self.config.api_key_encrypted:
            headers["Authorization"] = f"Bearer {CredentialEncryption().decrypt(self.config.api_key_encrypted)}"
        try:
            response = requests.post(
                f"{base}/chat/completions", headers=headers,
                json={"model": self.config.model_name, "temperature": self.config.temperature, "max_tokens": self.config.max_tokens_per_request, "messages": [{"role": "system", "content": request.system_prompt}, {"role": "user", "content": self._prompt(request)}]},
                timeout=120,
            )
            response.raise_for_status()
            raw = response.json()
            content = raw["choices"][0]["message"]["content"]
            usage = raw.get("usage", {})
            return ReviewResponse(content, [], raw, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), 0, int((monotonic() - started) * 1000))
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise ProviderError("AI provider request failed") from exc


class AnthropicProvider(BaseAIProvider):
    """Provider for Anthropic's messages API."""

    def review(self, request: ReviewRequest) -> ReviewResponse:
        """Call the Anthropic messages endpoint."""
        started = monotonic()
        base = self.config.api_base_url.rstrip("/") if self.config.api_base_url else "https://api.anthropic.com/v1"
        try:
            key = CredentialEncryption().decrypt(self.config.api_key_encrypted)
            response = requests.post(f"{base}/messages", headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={"model": self.config.model_name, "max_tokens": self.config.max_tokens_per_request, "temperature": self.config.temperature, "system": request.system_prompt, "messages": [{"role": "user", "content": self._prompt(request)}]}, timeout=120)
            response.raise_for_status()
            raw = response.json()
            content = "".join(part.get("text", "") for part in raw.get("content", []))
            usage = raw.get("usage", {})
            return ReviewResponse(content, [], raw, usage.get("input_tokens", 0), usage.get("output_tokens", 0), 0, int((monotonic() - started) * 1000))
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise ProviderError("AI provider request failed") from exc
