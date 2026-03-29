"""
Shared model factory — OpenRouter via OpenAI-compatible API.
"""

import os

from agno.models.openai import OpenAIChat


def llm() -> OpenAIChat:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    model   = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite")
    return OpenAIChat(
        id=model,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )
