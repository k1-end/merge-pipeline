"""Built-in AI providers."""

from apps.reviews.providers.http import AnthropicProvider, OpenAICompatibleProvider

PROVIDER_MAP = {
    "anthropic": AnthropicProvider,
    "openai": OpenAICompatibleProvider,
    "ollama": OpenAICompatibleProvider,
    "vllm": OpenAICompatibleProvider,
    "custom": OpenAICompatibleProvider,
}
