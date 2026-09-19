"""Built-in AI providers."""

from apps.reviews.providers.http import AnthropicProvider, OpenAICompatibleProvider
from apps.reviews.providers.opencode import OpenCodeProvider

PROVIDER_MAP = {
    "anthropic": AnthropicProvider,
    "openai": OpenAICompatibleProvider,
    "opencode": OpenCodeProvider,
    "ollama": OpenAICompatibleProvider,
    "vllm": OpenAICompatibleProvider,
    "custom": OpenAICompatibleProvider,
}
