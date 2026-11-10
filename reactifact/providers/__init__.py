"""Providers: contracts, lazy implementations and env factories.
Only contracts and deterministic fakes are imported eagerly; concrete
providers are loaded on first attribute access (PEP 562), so `import
reactifact` does not pay for every vendor module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .contracts import (
    EmbeddingProvider,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMResponseChunk,
    Message,
    Role,
)
from .fake import FakeEmbedder, FakeLLM

if TYPE_CHECKING:
    from .anthropic import AnthropicProvider, anthropic_llm
    from .azure import azure_llm
    from .cerebras import cerebras_llm
    from .chat import (
        OpenAICompatEmbedder,
        OpenAICompatProvider,
        embedder_from_env,
        llm_from_env,
    )
    from .deepseek import deepseek_llm
    from .fireworks import fireworks_embedder, fireworks_llm
    from .gemini import GeminiImageProvider, GeminiProvider, gemini_image, gemini_llm
    from .github_models import github_models_llm
    from .groq import groq_llm, groq_transcriber
    from .image import (
        ImageProvider,
        OpenAICompatImageProvider,
        OpenRouterImageProvider,
        image_from_env,
    )
    from .mistral import mistral_embedder, mistral_llm
    from .nvidia import nvidia_embedder, nvidia_nim_llm
    from .ollama import ollama_llm
    from .openai import openai_embedder, openai_llm
    from .openrouter import (
        openrouter_embedder,
        openrouter_image,
        openrouter_llm,
        openrouter_speech,
    )
    from .perplexity import perplexity_llm
    from .qwen import qwen_embedder, qwen_llm
    from .speech import (
        OpenAICompatSpeech,
        OpenAICompatTranscriber,
        SpeechProvider,
        TranscriberProvider,
        speech_from_env,
        transcriber_from_env,
    )
    from .together import together_embedder, together_llm
    from .video import (
        LumaVideoProvider,
        OpenRouterVideoProvider,
        RunwayVideoProvider,
        SoraVideoProvider,
        VideoProvider,
        VideoResult,
        video_from_env,
    )
    from .xai import xai_llm
    from .zai import zai_llm

_LAZY: dict[str, str] = {
    "AnthropicProvider": "anthropic",
    "GeminiImageProvider": "gemini",
    "GeminiProvider": "gemini",
    "ImageProvider": "image",
    "LumaVideoProvider": "video",
    "OpenAICompatEmbedder": "chat",
    "OpenAICompatImageProvider": "image",
    "OpenAICompatProvider": "chat",
    "OpenAICompatSpeech": "speech",
    "OpenAICompatTranscriber": "speech",
    "OpenRouterImageProvider": "image",
    "OpenRouterVideoProvider": "video",
    "RunwayVideoProvider": "video",
    "SoraVideoProvider": "video",
    "SpeechProvider": "speech",
    "TranscriberProvider": "speech",
    "VideoProvider": "video",
    "VideoResult": "video",
    "anthropic_llm": "anthropic",
    "azure_llm": "azure",
    "cerebras_llm": "cerebras",
    "deepseek_llm": "deepseek",
    "embedder_from_env": "chat",
    "fireworks_embedder": "fireworks",
    "fireworks_llm": "fireworks",
    "gemini_image": "gemini",
    "gemini_llm": "gemini",
    "github_models_llm": "github_models",
    "groq_llm": "groq",
    "groq_transcriber": "groq",
    "image_from_env": "image",
    "llm_from_env": "chat",
    "mistral_embedder": "mistral",
    "mistral_llm": "mistral",
    "nvidia_embedder": "nvidia",
    "nvidia_nim_llm": "nvidia",
    "ollama_llm": "ollama",
    "openai_embedder": "openai",
    "openai_llm": "openai",
    "openrouter_embedder": "openrouter",
    "openrouter_image": "openrouter",
    "openrouter_llm": "openrouter",
    "openrouter_speech": "openrouter",
    "perplexity_llm": "perplexity",
    "qwen_embedder": "qwen",
    "qwen_llm": "qwen",
    "speech_from_env": "speech",
    "together_embedder": "together",
    "together_llm": "together",
    "transcriber_from_env": "speech",
    "video_from_env": "video",
    "xai_llm": "xai",
    "zai_llm": "zai",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


def from_env(**overrides: Any) -> LLMProvider | None:
    """One-call provider selection: `OPENROUTER_API_KEY` first, else a local
    OpenAI-compatible endpoint via `OPENAI_BASE_URL`, else `None` (offline).
    """
    from .chat import llm_from_env
    from .openrouter import openrouter_llm

    return openrouter_llm(**overrides) or llm_from_env(**overrides)


__all__ = [
    "AnthropicProvider",
    "EmbeddingProvider",
    "FakeEmbedder",
    "FakeLLM",
    "GeminiImageProvider",
    "GeminiProvider",
    "ImageProvider",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LLMResponseChunk",
    "LumaVideoProvider",
    "Message",
    "OpenAICompatEmbedder",
    "OpenAICompatImageProvider",
    "OpenAICompatProvider",
    "OpenAICompatSpeech",
    "OpenAICompatTranscriber",
    "OpenRouterImageProvider",
    "OpenRouterVideoProvider",
    "Role",
    "RunwayVideoProvider",
    "SoraVideoProvider",
    "SpeechProvider",
    "TranscriberProvider",
    "VideoProvider",
    "VideoResult",
    "anthropic_llm",
    "azure_llm",
    "cerebras_llm",
    "deepseek_llm",
    "embedder_from_env",
    "fireworks_embedder",
    "fireworks_llm",
    "from_env",
    "gemini_image",
    "gemini_llm",
    "github_models_llm",
    "groq_llm",
    "groq_transcriber",
    "image_from_env",
    "llm_from_env",
    "mistral_embedder",
    "mistral_llm",
    "nvidia_embedder",
    "nvidia_nim_llm",
    "ollama_llm",
    "openai_embedder",
    "openai_llm",
    "openrouter_embedder",
    "openrouter_image",
    "openrouter_llm",
    "openrouter_speech",
    "perplexity_llm",
    "qwen_embedder",
    "qwen_llm",
    "speech_from_env",
    "together_embedder",
    "together_llm",
    "transcriber_from_env",
    "video_from_env",
    "xai_llm",
    "zai_llm",
]
