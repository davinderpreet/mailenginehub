"""
ai_provider.py — Multi-model AI API abstraction layer.

Provides a unified interface for calling different AI model APIs
(Anthropic Claude, OpenAI GPT, etc.) with a simple factory function.
"""

import os
import logging

from database import AIModelConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AIProviderError(Exception):
    """Raised when an AI API call fails."""

    def __init__(self, message, provider="", model_id=""):
        self.provider = provider
        self.model_id = model_id
        super().__init__(message)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class AIProvider:
    """Base class for AI model providers."""

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> str:
        """Send a prompt to the AI model and return the response as a plain string."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Anthropic (Claude)
# ---------------------------------------------------------------------------

class AnthropicProvider(AIProvider):
    """Claude API via the anthropic SDK (already installed)."""

    def __init__(self, api_key: str, model_id: str):
        self.api_key = api_key
        self.model_id = model_id

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> str:
        try:
            import anthropic
        except ImportError:
            raise AIProviderError(
                "The 'anthropic' package is not installed. "
                "Install it with: pip install anthropic",
                provider="anthropic",
                model_id=self.model_id,
            )

        try:
            client = anthropic.Anthropic(api_key=self.api_key)
            response = client.messages.create(
                model=self.model_id,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text
        except Exception as exc:
            raise AIProviderError(
                f"Anthropic API error: {exc}",
                provider="anthropic",
                model_id=self.model_id,
            ) from exc


# ---------------------------------------------------------------------------
# OpenAI (GPT)
# ---------------------------------------------------------------------------

class OpenAIProvider(AIProvider):
    """OpenAI API via the openai SDK."""

    def __init__(self, api_key: str, model_id: str):
        self.api_key = api_key
        self.model_id = model_id

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> str:
        try:
            import openai
        except ImportError:
            raise AIProviderError(
                "The 'openai' package is not installed. "
                "Install it with: pip install openai",
                provider="openai",
                model_id=self.model_id,
            )

        try:
            client = openai.OpenAI(api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model_id,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content
        except Exception as exc:
            raise AIProviderError(
                f"OpenAI API error: {exc}",
                provider="openai",
                model_id=self.model_id,
            ) from exc


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_PROVIDERS = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}

# Fallback defaults when no AIModelConfig row exists
_FALLBACK_PROVIDER = "anthropic"
_FALLBACK_MODEL = "claude-haiku-4-5-20251001"
_FALLBACK_API_KEY_ENV = "ANTHROPIC_API_KEY"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_provider(config=None) -> AIProvider:
    """
    Factory: returns a provider from an AIModelConfig row or the default.

    If *config* is ``None``, queries AIModelConfig for the default active
    model.  If no default is found, falls back to Anthropic with the
    ``ANTHROPIC_API_KEY`` environment variable and model
    ``claude-haiku-4-5-20251001``.

    The API key is read from ``os.environ`` using the config's
    ``api_key_env`` field.
    """

    provider_name: str
    model_id: str
    api_key_env: str

    if config is None:
        try:
            config = (
                AIModelConfig
                .select()
                .where(
                    AIModelConfig.is_default == True,   # noqa: E712
                    AIModelConfig.is_active == True,    # noqa: E712
                )
                .get()
            )
        except AIModelConfig.DoesNotExist:
            config = None

    if config is not None:
        provider_name = config.provider
        model_id = config.model_id
        api_key_env = config.api_key_env
    else:
        log.info("No default AIModelConfig found — using Anthropic fallback.")
        provider_name = _FALLBACK_PROVIDER
        model_id = _FALLBACK_MODEL
        api_key_env = _FALLBACK_API_KEY_ENV

    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise AIProviderError(
            f"Environment variable '{api_key_env}' is not set or empty.",
            provider=provider_name,
            model_id=model_id,
        )

    provider_cls = _PROVIDERS.get(provider_name)
    if provider_cls is None:
        raise AIProviderError(
            f"Unknown provider '{provider_name}'. "
            f"Supported: {', '.join(sorted(_PROVIDERS))}",
            provider=provider_name,
            model_id=model_id,
        )

    return provider_cls(api_key=api_key, model_id=model_id)
