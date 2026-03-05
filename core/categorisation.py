"""
core/categorisation.py
Categorisation pipeline (applied in strict order):
  1. Check final_category override in DB
  2. Exact case-insensitive keyword match from categories.json
  3. Fuzzy match using rapidfuzz token_sort_ratio >= 75
  4. LLM (if configured)
  5. Fallback: Uncategorised
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz, process

# ── Load categories ───────────────────────────────────────────────────────────
CATEGORIES_PATH = Path(__file__).resolve().parent.parent / "data" / "categories.json"

_categories_cache: Optional[List[Dict]] = None
_keyword_index_cache: Optional[List] = None


def load_categories() -> List[Dict]:
    global _categories_cache
    if _categories_cache is None:
        _categories_cache = _read_categories()
    return _categories_cache


def _read_categories() -> List[Dict]:
    try:
        with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("categories", [])
    except Exception:
        return []


def reload_categories() -> None:
    global _categories_cache, _keyword_index_cache
    _categories_cache = _read_categories()
    _keyword_index_cache = None  # force index rebuild


def save_categories(categories: List[Dict]) -> None:
    with open(CATEGORIES_PATH, "w", encoding="utf-8") as f:
        json.dump({"categories": categories}, f, indent=2, ensure_ascii=False)
    reload_categories()


def add_keyword_rule(
    category: str,
    subcategory: str,
    type_tag: str,
    keyword: str,
) -> None:
    """Add a keyword to an existing or new subcategory in categories.json."""
    cats = load_categories()
    for cat in cats:
        if cat["category"].lower() == category.lower():
            for sub in cat["subcategories"]:
                if sub["subcategory"].lower() == subcategory.lower():
                    if keyword not in sub["keywords"]:
                        sub["keywords"].append(keyword)
                    save_categories(cats)
                    return
            # Subcategory doesn't exist, create it
            cat["subcategories"].append({
                "subcategory": subcategory,
                "type_tag": type_tag,
                "keywords": [keyword],
            })
            save_categories(cats)
            return
    # Category doesn't exist, create it
    cats.append({
        "category": category,
        "subcategories": [{
            "subcategory": subcategory,
            "type_tag": type_tag,
            "keywords": [keyword],
        }],
    })
    save_categories(cats)


# ── Build flat keyword index ──────────────────────────────────────────────────
# Each entry: (keyword_upper, category, subcategory, type_tag)
KeywordEntry = Tuple[str, str, str, str]


def _build_keyword_index() -> List[KeywordEntry]:
    index: List[KeywordEntry] = []
    for cat in load_categories():
        category = cat["category"]
        for sub in cat.get("subcategories", []):
            subcategory = sub["subcategory"]
            type_tag = sub.get("type_tag", "Misc")
            for kw in sub.get("keywords", []):
                index.append((kw.upper(), category, subcategory, type_tag))
    return index


def get_keyword_index() -> List[KeywordEntry]:
    global _keyword_index_cache
    if _keyword_index_cache is None:
        _keyword_index_cache = _build_keyword_index()
    return _keyword_index_cache


# ── Step 2: Exact keyword match ────────────────────────────────────────────────

def exact_match(description: str) -> Optional[Dict[str, Any]]:
    """Case-insensitive substring match against all keywords."""
    desc_upper = description.upper()
    index = get_keyword_index()
    for kw, category, subcategory, type_tag in index:
        if kw in desc_upper:
            return {
                "category": category,
                "subcategory": subcategory,
                "type_tag": type_tag,
                "confidence": 0.95,
                "method": "exact",
            }
    return None


# ── Step 3: Fuzzy match ────────────────────────────────────────────────────────

FUZZY_THRESHOLD = 65          # token_sort_ratio fallback threshold
PARTIAL_RATIO_THRESHOLD = 82  # partial_ratio: keyword as near-exact substring


def fuzzy_match(description: str) -> Optional[Dict[str, Any]]:
    """
    Two-pass fuzzy match:
    1. partial_ratio >= 82  — keyword appears as near-exact substring (handles
       long descriptions with extra reference numbers / codes)
    2. token_sort_ratio >= 65 — word-order-agnostic similarity fallback
    """
    index = get_keyword_index()
    if not index:
        return None

    keywords = [entry[0] for entry in index]
    desc_upper = description.upper()

    # Pass 1: partial_ratio — best for short keywords in long descriptions
    results = process.extract(
        desc_upper,
        keywords,
        scorer=fuzz.partial_ratio,
        limit=1,
        score_cutoff=PARTIAL_RATIO_THRESHOLD,
    )
    if not results:
        # Pass 2: token_sort_ratio — catches word-order / token variations
        results = process.extract(
            desc_upper,
            keywords,
            scorer=fuzz.token_sort_ratio,
            limit=1,
            score_cutoff=FUZZY_THRESHOLD,
        )
    if not results:
        return None

    best_kw, score, idx = results[0]
    _, category, subcategory, type_tag = index[idx]

    return {
        "category": category,
        "subcategory": subcategory,
        "type_tag": type_tag,
        "confidence": round(score / 100.0, 3),
        "method": "fuzzy",
    }


# ── Main pipeline ──────────────────────────────────────────────────────────────

FALLBACK_RESULT = {
    "category": "Uncategorised",
    "subcategory": "Misc",
    "type_tag": "Misc",
    "confidence": 0.0,
    "method": "fallback",
}


def categorise_transaction(
    description: str,
    amount: Optional[float] = None,
    existing_final_category: Optional[str] = None,
    existing_final_subcategory: Optional[str] = None,
    existing_final_type_tag: Optional[str] = None,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """
    Full categorisation pipeline.
    Returns dict with category, subcategory, type_tag, confidence, method.
    """
    # Step 1: User override already in DB (caller checks this before calling)
    if existing_final_category:
        return {
            "category": existing_final_category,
            "subcategory": existing_final_subcategory or "Misc",
            "type_tag": existing_final_type_tag or "Misc",
            "confidence": 1.0,
            "method": "override",
        }

    # Step 2: Exact keyword match
    result = exact_match(description)
    if result:
        return result

    # Step 3: Fuzzy match
    result = fuzzy_match(description)
    if result:
        return result

    # Step 4: LLM (if available)
    if use_llm:
        try:
            from core.llm import categorise_with_llm
            llm_result = categorise_with_llm(description, amount, load_categories())
            if llm_result and llm_result.get("category") != "Uncategorised":
                llm_result["method"] = "llm"
                return llm_result
        except Exception:
            pass

    # Step 5: Fallback
    return dict(FALLBACK_RESULT)


def categorise_batch(
    transactions: List[Dict[str, Any]],
    progress_callback=None,
    use_llm: bool = True,
) -> List[Dict[str, Any]]:
    """
    Categorise a list of transaction dicts in place.
    Each dict is updated with category, subcategory, type_tag, ai_confidence.
    Returns updated transactions.
    """
    total = len(transactions)
    updated = []

    for i, tx in enumerate(transactions):
        if progress_callback:
            progress_callback(i / max(total, 1))

        # If final_category is already set, skip re-categorisation
        if tx.get("final_category"):
            updated.append(tx)
            continue

        result = categorise_transaction(
            description=tx.get("description", ""),
            amount=tx.get("net_amount"),
            existing_final_category=tx.get("final_category"),
            existing_final_subcategory=tx.get("final_subcategory"),
            existing_final_type_tag=tx.get("final_type_tag"),
            use_llm=use_llm,
        )

        tx["category"] = result["category"]
        tx["subcategory"] = result["subcategory"]
        tx["type_tag"] = result["type_tag"]
        tx["ai_confidence"] = result["confidence"]
        updated.append(tx)

    return updated


def categorise_and_save(
    tx_ids: Optional[List[str]] = None,
    progress_callback=None,
    use_llm: bool = True,
) -> int:
    """
    Run categorisation pipeline for all (or specified) uncategorised transactions
    and persist results to DB. Returns count updated.
    """
    from core.database import get_transactions, update_transaction

    if tx_ids:
        txs = [
            t for t in get_transactions()
            if t["id"] in set(tx_ids)
        ]
    else:
        txs = [
            t for t in get_transactions()
            if not t.get("final_category")
            and t.get("category") in (None, "", "Uncategorised")
        ]

    updated_count = 0
    total = len(txs)

    for i, tx in enumerate(txs):
        if progress_callback:
            progress_callback(i / max(total, 1))

        result = categorise_transaction(
            description=tx.get("description", ""),
            amount=tx.get("net_amount"),
            use_llm=use_llm,
        )

        update_transaction(tx["id"], {
            "category": result["category"],
            "subcategory": result["subcategory"],
            "type_tag": result["type_tag"],
            "ai_confidence": result["confidence"],
        })
        updated_count += 1

    return updated_count


def get_all_category_names() -> List[str]:
    return sorted({cat["category"] for cat in load_categories()})


def get_subcategories_for(category: str) -> List[str]:
    for cat in load_categories():
        if cat["category"] == category:
            return sorted({sub["subcategory"] for sub in cat.get("subcategories", [])})
    return []


def get_effective_category(tx: Dict[str, Any]) -> Tuple[str, str, str]:
    """Return (category, subcategory, type_tag) respecting user overrides."""
    cat = tx.get("final_category") or tx.get("category") or "Uncategorised"
    sub = tx.get("final_subcategory") or tx.get("subcategory") or "Misc"
    tag = tx.get("final_type_tag") or tx.get("type_tag") or "Misc"
    return cat, sub, tag
