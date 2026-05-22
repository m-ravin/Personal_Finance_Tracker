"""
LLM categorisation tests — two tiers:

TIER 1 — Unit (always run, no real API calls):
  - _build_prompt        : prompt contains all required content
  - _parse_llm_response  : handles valid JSON, markdown fences, invalid JSON
  - categorise_with_llm  : falls back gracefully when no settings / no key
  - categorise_with_llm  : routes correctly when mocked (Claude / OpenAI / Groq)
  - categorise_transaction with use_llm=True + mocked LLM

TIER 2 — Integration (auto-skipped when API key absent):
  - Real Claude call for merchants not in categories.json
  - Real OpenAI call
  - Real Groq call
  - Full pipeline (categorise_transaction) reaches 'llm' method

Add API keys to .env to activate Tier 2:
  ANTHROPIC_API_KEY=sk-ant-...
  OPENAI_API_KEY=sk-...
  GROQ_API_KEY=gsk_...
"""
import os
import json
import pytest
from unittest.mock import patch

from core.llm import (
    _build_prompt, _parse_llm_response,
    categorise_with_llm, FALLBACK,
)
from core.categorisation import categorise_transaction, load_categories

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY") or ""
OPENAI_KEY    = os.environ.get("OPENAI_API_KEY") or ""
GROQ_KEY      = os.environ.get("GROQ_API_KEY") or ""

CATEGORIES = load_categories()

# Merchants from the sample files that have no keyword match
UNKNOWN_MERCHANTS = [
    ("CR Roofoods Limited",                                       166.07),
    ("CR Just Eat.co.uk Lim JEA21386763",                          94.39),
    ("BOOKER LTD - 38588424 - WELLINGBOROUG - Card Ending: 2183", 218.12),
    ("BESTWAY WHOLESALE - GREAT BRITAI - Card Ending: 2183",       58.82),
    ("UNIVERSAL EXPRESS DIST - Isleworth - Card Ending: 2183",    357.88),
    ("Madina Butchers - London - Card Ending: 2183",               71.33),
    ("Payment made (VirtualBankTransfer)",                        -400.00),
    ("DD CASTLE WATER LTD",                                        119.59),
]


# ═════════════════════════════════════════════════════════════════════════════
# TIER 1 — Unit tests (always run, all mocked)
# ═════════════════════════════════════════════════════════════════════════════

class TestBuildPrompt:
    def test_description_in_prompt(self):
        prompt = _build_prompt("CR Roofoods Limited", 166.07, CATEGORIES)
        assert "CR Roofoods Limited" in prompt

    def test_amount_in_prompt(self):
        prompt = _build_prompt("CR Roofoods Limited", 166.07, CATEGORIES)
        assert "166.07" in prompt

    def test_amount_unknown_when_none(self):
        prompt = _build_prompt("CR Roofoods Limited", None, CATEGORIES)
        assert "unknown" in prompt.lower()

    def test_categories_listed_in_prompt(self):
        prompt = _build_prompt("BOOKER LTD", 218.12, CATEGORIES)
        assert "Food & Dining" in prompt or "Shopping" in prompt

    def test_prompt_instructs_json_output(self):
        prompt = _build_prompt("test", 1.0, CATEGORIES)
        assert "JSON" in prompt
        assert "confidence" in prompt


class TestParseLLMResponse:
    def test_valid_json_parsed(self):
        raw = json.dumps({
            "category": "Food & Dining",
            "subcategory": "Delivery",
            "type_tag": "Delivery",
            "confidence": 0.92,
        })
        result = _parse_llm_response(raw)
        assert result["category"] == "Food & Dining"
        assert result["subcategory"] == "Delivery"
        assert result["confidence"] == pytest.approx(0.92)

    def test_markdown_json_fence_stripped(self):
        raw = '```json\n{"category": "Shopping", "subcategory": "Wholesale", "type_tag": "Misc", "confidence": 0.8}\n```'
        result = _parse_llm_response(raw)
        assert result["category"] == "Shopping"

    def test_plain_backtick_fence_stripped(self):
        raw = '```\n{"category": "Utilities", "subcategory": "Bills", "type_tag": "Recurring", "confidence": 0.7}\n```'
        result = _parse_llm_response(raw)
        assert result["category"] == "Utilities"

    def test_invalid_json_returns_fallback(self):
        result = _parse_llm_response("Sorry, I cannot categorise this.")
        assert result["category"] == "Uncategorised"

    def test_missing_fields_get_defaults(self):
        raw = '{"category": "Financial"}'
        result = _parse_llm_response(raw)
        assert result["category"] == "Financial"
        assert result["subcategory"] == "Misc"
        assert result["type_tag"] == "Misc"
        assert result["confidence"] == pytest.approx(0.5)

    def test_all_required_keys_always_present(self):
        for raw in [
            '{"category": "Food & Dining", "subcategory": "Delivery", "type_tag": "Delivery", "confidence": 0.9}',
            "not json at all",
            "{}",
        ]:
            result = _parse_llm_response(raw)
            for key in ("category", "subcategory", "type_tag", "confidence"):
                assert key in result, f"Key '{key}' missing for input: {raw!r}"


class TestCategoriseWithLLMFallbacks:
    # get_active_llm_settings is a local import inside categorise_with_llm,
    # so we must patch it at its source module: core.database
    def test_returns_fallback_when_no_db_settings(self):
        with patch("core.database.get_active_llm_settings", return_value=None):
            result = categorise_with_llm("BOOKER LTD", 218.12, CATEGORIES)
        assert result["category"] == "Uncategorised"

    def test_returns_fallback_when_provider_is_none(self):
        with patch("core.database.get_active_llm_settings",
                   return_value={"provider": "none", "model": ""}):
            result = categorise_with_llm("BOOKER LTD", 218.12, CATEGORIES)
        assert result["category"] == "Uncategorised"

    def test_returns_fallback_when_no_api_key(self):
        with patch("core.database.get_active_llm_settings",
                   return_value={"provider": "claude", "model": "claude-3-5-haiku-20241022"}), \
             patch("core.llm._get_api_key", return_value=None):
            result = categorise_with_llm("BOOKER LTD", 218.12, CATEGORIES)
        assert result["category"] == "Uncategorised"

    def test_returns_fallback_when_db_raises(self):
        with patch("core.database.get_active_llm_settings",
                   side_effect=Exception("DB error")):
            result = categorise_with_llm("BOOKER LTD", 218.12, CATEGORIES)
        assert result["category"] == "Uncategorised"


class TestCategoriseWithLLMMocked:
    _claude  = {"provider": "claude",  "model": "claude-3-5-haiku-20241022"}
    _openai  = {"provider": "openai",  "model": "gpt-4o-mini"}
    _groq    = {"provider": "groq",    "model": "llama3-70b-8192"}

    def _resp(self, cat, sub="Misc", tag="Misc", conf=0.88):
        return {"category": cat, "subcategory": sub, "type_tag": tag, "confidence": conf}

    def test_claude_route_called(self):
        with patch("core.database.get_active_llm_settings", return_value=self._claude), \
             patch("core.llm._get_api_key", return_value="sk-test"), \
             patch("core.llm._call_claude",
                   return_value=self._resp("Food & Dining", "Delivery")) as m:
            result = categorise_with_llm("CR Roofoods Limited", 166.07, CATEGORIES)
        m.assert_called_once()
        assert result["category"] == "Food & Dining"

    def test_openai_route_called(self):
        with patch("core.database.get_active_llm_settings", return_value=self._openai), \
             patch("core.llm._get_api_key", return_value="sk-test"), \
             patch("core.llm._call_openai",
                   return_value=self._resp("Shopping", "Wholesale")) as m:
            result = categorise_with_llm("BOOKER LTD", 218.12, CATEGORIES)
        m.assert_called_once()
        assert result["category"] == "Shopping"

    def test_groq_route_called(self):
        with patch("core.database.get_active_llm_settings", return_value=self._groq), \
             patch("core.llm._get_api_key", return_value="gsk-test"), \
             patch("core.llm._call_groq",
                   return_value=self._resp("Utilities", "Bills")) as m:
            result = categorise_with_llm("DD CASTLE WATER LTD", 119.59, CATEGORIES)
        m.assert_called_once()
        assert result["category"] == "Utilities"

    def test_provider_exception_returns_fallback(self):
        with patch("core.database.get_active_llm_settings", return_value=self._claude), \
             patch("core.llm._get_api_key", return_value="sk-test"), \
             patch("core.llm._call_claude", side_effect=Exception("timeout")):
            result = categorise_with_llm("CR Roofoods Limited", 166.07, CATEGORIES)
        assert result["category"] == "Uncategorised"

    @pytest.mark.parametrize("description,amount", UNKNOWN_MERCHANTS)
    def test_all_unknown_merchants_handled(self, description, amount):
        with patch("core.database.get_active_llm_settings", return_value=self._claude), \
             patch("core.llm._get_api_key", return_value="sk-test"), \
             patch("core.llm._call_claude", return_value=self._resp("Food & Dining")):
            result = categorise_with_llm(description, amount, CATEGORIES)
        assert "category" in result


class TestFullPipelineWithMockedLLM:
    # categorise_with_llm is a local import inside categorise_transaction,
    # so we patch it at its definition: core.llm
    def test_method_is_llm_when_llm_succeeds(self):
        llm_resp = {"category": "Food & Dining", "subcategory": "Delivery",
                    "type_tag": "Delivery", "confidence": 0.9}
        with patch("core.llm.categorise_with_llm", return_value=llm_resp):
            result = categorise_transaction("CR Roofoods Limited",
                                            amount=166.07, use_llm=True)
        assert result["method"] == "llm"
        assert result["category"] == "Food & Dining"

    def test_pipeline_falls_back_when_llm_returns_uncategorised(self):
        with patch("core.llm.categorise_with_llm",
                   return_value={"category": "Uncategorised", "subcategory": "Misc",
                                 "type_tag": "Misc", "confidence": 0.0}):
            result = categorise_transaction("Gibberish XYZ123", use_llm=True)
        assert result["category"] == "Uncategorised"
        assert result["method"] == "fallback"

    def test_exact_match_never_reaches_llm(self):
        """'Interest Charge' hits keyword → LLM must not be called at all."""
        with patch("core.llm.categorise_with_llm") as mock_llm:
            result = categorise_transaction(
                "Interest Charge (03/04/2026 - 02/05/2026)", use_llm=True
            )
        mock_llm.assert_not_called()
        assert result["method"] == "exact"
        assert result["category"] == "Financial"


# ═════════════════════════════════════════════════════════════════════════════
# TIER 2 — Real API integration (auto-skipped when key absent)
# ═════════════════════════════════════════════════════════════════════════════

def _real_call(provider: str, key: str, description: str, amount: float):
    from core.llm import _build_prompt, _call_claude, _call_openai, _call_groq
    prompt = _build_prompt(description, amount, CATEGORIES)
    if provider == "claude":
        return _call_claude(key, "claude-3-5-haiku-20241022", prompt)
    if provider == "openai":
        return _call_openai(key, "gpt-4o-mini", prompt)
    if provider == "groq":
        return _call_groq(key, "llama3-70b-8192", prompt)


def _assert_valid_llm_result(result, description):
    for key in ("category", "subcategory", "type_tag", "confidence"):
        assert key in result, f"Key '{key}' missing for: {description}"
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["category"] != "Uncategorised", (
        f"LLM returned Uncategorised for known merchant: {description!r}"
    )


@pytest.mark.skipif(not ANTHROPIC_KEY, reason="ANTHROPIC_API_KEY not set in .env")
class TestRealClaude:
    @pytest.mark.parametrize("description,amount", UNKNOWN_MERCHANTS)
    def test_categorises_merchant(self, description, amount):
        result = _real_call("claude", ANTHROPIC_KEY, description, amount)
        _assert_valid_llm_result(result, description)

    def test_full_pipeline_method_is_llm(self):
        with patch("core.llm.get_active_llm_settings",
                   return_value={"provider": "claude",
                                 "model": "claude-3-5-haiku-20241022"}), \
             patch("core.llm._get_api_key", return_value=ANTHROPIC_KEY):
            result = categorise_transaction(
                "BOOKER LTD - 38588424 - WELLINGBOROUG",
                amount=218.12, use_llm=True,
            )
        assert result["method"] == "llm"
        assert result["category"] != "Uncategorised"


@pytest.mark.skipif(not OPENAI_KEY, reason="OPENAI_API_KEY not set in .env")
class TestRealOpenAI:
    @pytest.mark.parametrize("description,amount", UNKNOWN_MERCHANTS[:3])
    def test_categorises_merchant(self, description, amount):
        result = _real_call("openai", OPENAI_KEY, description, amount)
        _assert_valid_llm_result(result, description)


@pytest.mark.skipif(not GROQ_KEY, reason="GROQ_API_KEY not set in .env")
class TestRealGroq:
    @pytest.mark.parametrize("description,amount", UNKNOWN_MERCHANTS[:3])
    def test_categorises_merchant(self, description, amount):
        result = _real_call("groq", GROQ_KEY, description, amount)
        _assert_valid_llm_result(result, description)
