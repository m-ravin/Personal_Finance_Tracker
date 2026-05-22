"""
Tests for CC sign logic in core/ingestion.py apply_mapping().

Bug reproduced: when a CC statement has positive and negative amounts
with no separate sign column, the old code called abs(amt) before
checking sign, making everything debit.

Expected behaviour:
  - No sign column + positive amt  → credit
  - No sign column + negative amt  → debit
  - Sign column "CR"               → credit (regardless of amt sign)
  - Sign column "DR"               → debit  (regardless of amt sign)
"""
import pytest
from core.ingestion import apply_mapping


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_rows(*rows):
    import pandas as pd
    return pd.DataFrame(rows, dtype=str)


def _run(df, mapping, account_type="credit_card"):
    valid, failed = apply_mapping(
        df, mapping,
        account_name="TestCC",
        account_type=account_type,
        source_file="test.csv",
    )
    return valid, failed


# ── no sign column: positive amount ──────────────────────────────────────────

def test_no_sign_column_positive_amount_is_credit():
    """Positive amount with no sign column → credit."""
    df = _make_rows({"Date": "2026-01-10", "Desc": "Refund", "Amount": "500.00"})
    mapping = {"Date": "date", "Desc": "description", "Amount": "amount"}
    valid, _ = _run(df, mapping)

    assert len(valid) == 1
    row = valid[0]
    assert row["credit"] == 500.0
    assert row["debit"] is None
    assert row["net_amount"] == 500.0


# ── no sign column: negative amount ──────────────────────────────────────────

def test_no_sign_column_negative_amount_is_debit():
    """Negative amount with no sign column → debit."""
    df = _make_rows({"Date": "2026-01-11", "Desc": "Purchase", "Amount": "-250.00"})
    mapping = {"Date": "date", "Desc": "description", "Amount": "amount"}
    valid, _ = _run(df, mapping)

    assert len(valid) == 1
    row = valid[0]
    assert row["debit"] == 250.0
    assert row["credit"] is None
    assert row["net_amount"] == -250.0


# ── mixed positive + negative rows ───────────────────────────────────────────

def test_mixed_signs_without_sign_column():
    """Mixed positive and negative amounts in one upload, no sign column."""
    df = _make_rows(
        {"Date": "2026-01-10", "Desc": "Refund",   "Amount": "100.00"},
        {"Date": "2026-01-11", "Desc": "Purchase",  "Amount": "-200.00"},
        {"Date": "2026-01-12", "Desc": "Cashback",  "Amount": "50.00"},
        {"Date": "2026-01-13", "Desc": "Grocery",   "Amount": "-75.50"},
    )
    mapping = {"Date": "date", "Desc": "description", "Amount": "amount"}
    valid, _ = _run(df, mapping)

    assert len(valid) == 4
    assert valid[0]["credit"] == 100.0  and valid[0]["debit"] is None
    assert valid[1]["debit"]  == 200.0  and valid[1]["credit"] is None
    assert valid[2]["credit"] == 50.0   and valid[2]["debit"] is None
    assert valid[3]["debit"]  == 75.50  and valid[3]["credit"] is None


# ── explicit sign column: CR ──────────────────────────────────────────────────

def test_explicit_cr_sign_overrides_amount_sign():
    """Sign column = 'CR' → credit."""
    df = _make_rows({
        "Date": "2026-01-15", "Desc": "Payment",
        "Amount": "300.00", "Sign": "CR",
    })
    mapping = {"Date": "date", "Desc": "description", "Amount": "amount", "Sign": "transaction_type"}
    valid, _ = _run(df, mapping)

    assert valid[0]["credit"] == 300.0
    assert valid[0]["debit"] is None


# ── explicit sign column: DR ──────────────────────────────────────────────────

def test_explicit_dr_sign_overrides_amount_sign():
    """Sign column = 'DR' → debit."""
    df = _make_rows({
        "Date": "2026-01-16", "Desc": "Charge",
        "Amount": "150.00", "Sign": "DR",
    })
    mapping = {"Date": "date", "Desc": "description", "Amount": "amount", "Sign": "transaction_type"}
    valid, _ = _run(df, mapping)

    assert valid[0]["debit"] == 150.0
    assert valid[0]["credit"] is None


# ── sign column long-form CREDIT / DEBIT ─────────────────────────────────────

def test_sign_column_long_form_credit():
    df = _make_rows({
        "Date": "2026-02-01", "Desc": "Refund",
        "Amount": "80.00", "Sign": "CREDIT",
    })
    mapping = {"Date": "date", "Desc": "description", "Amount": "amount", "Sign": "transaction_type"}
    valid, _ = _run(df, mapping)
    assert valid[0]["credit"] == 80.0


def test_sign_column_long_form_debit():
    df = _make_rows({
        "Date": "2026-02-02", "Desc": "Shopping",
        "Amount": "120.00", "Sign": "DEBIT",
    })
    mapping = {"Date": "date", "Desc": "description", "Amount": "amount", "Sign": "transaction_type"}
    valid, _ = _run(df, mapping)
    assert valid[0]["debit"] == 120.0


# ── net_amount integrity ──────────────────────────────────────────────────────

def test_net_amount_equals_credit_minus_debit():
    """net_amount must always equal credit - debit."""
    df = _make_rows(
        {"Date": "2026-03-01", "Desc": "A", "Amount": "200.00"},
        {"Date": "2026-03-02", "Desc": "B", "Amount": "-100.00"},
    )
    mapping = {"Date": "date", "Desc": "description", "Amount": "amount"}
    valid, _ = _run(df, mapping)

    for row in valid:
        expected = (row["credit"] or 0.0) - (row["debit"] or 0.0)
        assert row["net_amount"] == pytest.approx(expected)
