from __future__ import annotations

import os


def build_chat_model():
    """Return a ChatOpenAI-compatible model when provider env vars are present."""
    from dotenv import load_dotenv
    from langchain_openai import ChatOpenAI

    load_dotenv()

    model = os.getenv("LANGGRAPH_MODEL", "MiniMax-M2.7")
    base_url = os.getenv("LANGGRAPH_BASE_URL", "https://api.minimax.io/v1")
    api_key = os.getenv("MINIMAX_API_KEY") or os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "Set MINIMAX_API_KEY or OPENAI_API_KEY before using provider-backed calls."
        )

    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=0,
    )
