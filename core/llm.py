"""
core/llm.py
Multi-provider LLM abstraction for transaction categorisation.
Providers: Anthropic Claude | OpenAI | Groq | None (fallback)

API keys are read from st.session_state (runtime) or .env file.
Never stored in SQLite (only last-4-char hint is stored).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

FALLBACK = {
    "category": "Uncategorised",
    "subcategory": "Misc",
    "type_tag": "Misc",
    "confidence": 0.0,
}

PROVIDER_MODELS = {
    "claude": ["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022", "claude-3-opus-20240229"],
    "openai": ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"],
    "groq": ["llama3-70b-8192", "llama3-8b-8192", "mixtral-8x7b-32768"],
}


def _get_api_key(provider: str) -> Optional[str]:
    """Get API key from session_state first, then .env."""
    try:
        import streamlit as st
        key = st.session_state.get(f"{provider}_api_key")
        if key:
            return key
    except Exception:
        pass

    env_map = {
        "claude": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "groq": "GROQ_API_KEY",
    }
    return os.environ.get(env_map.get(provider, ""), None)


def _build_prompt(description: str, amount: Optional[float], categories: List[Dict]) -> str:
    """Build the categorisation prompt."""
    cat_list = []
    for cat in categories:
        for sub in cat.get("subcategories", []):
            cat_list.append(
                f"  - {cat['category']} > {sub['subcategory']} [{sub.get('type_tag', 'Misc')}]"
            )
    categories_text = "\n".join(cat_list[:80])  # Limit to avoid token overflow

    amount_text = f"Amount: {amount:.2f}" if amount is not None else "Amount: unknown"

    return (
        f"You are a personal finance categorisation assistant.\n"
        f"Classify this bank transaction into one of the categories below.\n\n"
        f"Transaction description: {description}\n"
        f"{amount_text}\n\n"
        f"Available categories (Category > Subcategory [TypeTag]):\n"
        f"{categories_text}\n\n"
        f"Respond ONLY with valid JSON (no markdown, no explanation):\n"
        f'{{"category": "...", "subcategory": "...", "type_tag": "...", "confidence": 0.0-1.0}}'
    )


def _parse_llm_response(text: str) -> Dict[str, Any]:
    """Parse JSON from LLM response, strip markdown fences if needed."""
    text = text.strip()
    # Remove markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
    try:
        data = json.loads(text)
        return {
            "category": str(data.get("category", "Uncategorised")),
            "subcategory": str(data.get("subcategory", "Misc")),
            "type_tag": str(data.get("type_tag", "Misc")),
            "confidence": float(data.get("confidence", 0.5)),
        }
    except (json.JSONDecodeError, ValueError):
        return dict(FALLBACK)


def categorise_with_llm(
    description: str,
    amount: Optional[float],
    categories: List[Dict],
) -> Dict[str, Any]:
    """
    Main entry point. Reads active provider from DB and calls the right SDK.
    Falls back gracefully if provider is 'none' or call fails.
    """
    try:
        from core.database import get_active_llm_settings
        settings = get_active_llm_settings()
    except Exception:
        return dict(FALLBACK)

    if not settings or settings.get("provider") == "none":
        return dict(FALLBACK)

    provider = settings["provider"]
    model = settings["model"]
    api_key = _get_api_key(provider)

    if not api_key:
        return dict(FALLBACK)

    prompt = _build_prompt(description, amount, categories)

    try:
        if provider == "claude":
            return _call_claude(api_key, model, prompt)
        elif provider == "openai":
            return _call_openai(api_key, model, prompt)
        elif provider == "groq":
            return _call_groq(api_key, model, prompt)
        else:
            return dict(FALLBACK)
    except Exception as e:
        try:
            import streamlit as st
            st.toast(f"LLM call failed ({provider}): {e}", icon="⚠️")
        except Exception:
            pass
        return dict(FALLBACK)


def _call_claude(api_key: str, model: str, prompt: str) -> Dict[str, Any]:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text
    return _parse_llm_response(text)


def _call_openai(api_key: str, model: str, prompt: str) -> Dict[str, Any]:
    import openai
    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=256,
        temperature=0.1,
    )
    text = response.choices[0].message.content or ""
    return _parse_llm_response(text)


def _call_groq(api_key: str, model: str, prompt: str) -> Dict[str, Any]:
    import groq
    client = groq.Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=256,
        temperature=0.1,
    )
    text = response.choices[0].message.content or ""
    return _parse_llm_response(text)


def test_llm_connection(
    provider: str,
    api_key: str,
    model: str,
) -> Dict[str, Any]:
    """
    Send a trivial test request. Returns latency + response.
    Used by Settings page "Test Connection" button.
    """
    test_desc = "AMAZON PAY"
    test_amount = 500.0

    from core.categorisation import load_categories
    categories = load_categories()
    prompt = _build_prompt(test_desc, test_amount, categories)

    start = time.time()
    try:
        if provider == "claude":
            result = _call_claude(api_key, model, prompt)
        elif provider == "openai":
            result = _call_openai(api_key, model, prompt)
        elif provider == "groq":
            result = _call_groq(api_key, model, prompt)
        else:
            return {"success": False, "error": "Unknown provider", "latency_ms": 0}
        latency_ms = int((time.time() - start) * 1000)
        return {"success": True, "result": result, "latency_ms": latency_ms}
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        return {"success": False, "error": str(e), "latency_ms": latency_ms}
