"""
core/reconciliation.py
Three reconciliation engines:
  A. Internal transfers (bank ↔ bank)
  B. Credit card payments (bank debit ↔ CC credit)
  C. Personal loans (manual tag + fuzzy contact match)
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz

from core.database import (
    get_transactions,
    get_reconciliation_pairs,
    upsert_reconciliation_pair,
    update_reconciliation_pair,
    update_transaction,
    get_loan_tags,
    update_loan_tag,
    create_loan_tag,
    delete_reconciliation_pairs_by_type,
)

# ── Constants ─────────────────────────────────────────────────────────────────
TRANSFER_DAY_WINDOW = 3
CC_PAYMENT_DAY_WINDOW = 5
AMOUNT_TOLERANCE = 0.005   # 0.5%

CC_KEYWORDS = [
    # Generic CC payment terms
    "GPAY-CREDITCARD", "CREDITCARD PAYMENT", "CRED",
    "NEFT CC", "AUTOPAY CC", "CC PAYMENT", "CREDIT CARD",
    "BILLDESK", "BILL PAY", "CCBILLPAY", "CC BILL",
    # Bank-specific CC payment references
    "GPAY CREDIT", "HDFC CRDT", "HDFC CC", "SBI CARD", "SBI CC",
    "ICICI CREDIT", "ICICI CC", "AXIS CC", "KOTAK CC",
    "AMEX PAYMENT", "INDUSIND CC",
]

# Keywords found in CC statement credits that indicate a bill payment (not a refund)
CC_PAYMENT_CREDIT_KEYWORDS = [
    "PAYMENT RECEIVED", "PAYMENT-THANK", "PAYMENT BY",
    "BILL PAYMENT", "ONLINE PAYMENT", "PAYMENT POSTED",
    "PAYMENT CREDITED",
]

LOAN_FUZZY_THRESHOLD = 80


def _amounts_match(a: float, b: float, tol: float = AMOUNT_TOLERANCE) -> bool:
    if a == 0 and b == 0:
        return True
    avg = (abs(a) + abs(b)) / 2.0
    if avg == 0:
        return False
    return abs(abs(a) - abs(b)) / avg <= tol


def _dates_within(d1: Any, d2: Any, days: int) -> bool:
    if d1 is None or d2 is None:
        return False
    if isinstance(d1, str):
        from datetime import datetime
        d1 = datetime.strptime(d1, "%Y-%m-%d").date()
    if isinstance(d2, str):
        from datetime import datetime
        d2 = datetime.strptime(d2, "%Y-%m-%d").date()
    return abs((d1 - d2).days) <= days


# ── A. Internal Transfer Detection ────────────────────────────────────────────

def find_internal_transfers(
    progress_callback=None,
) -> List[Dict[str, Any]]:
    """
    Find candidate transfer pairs:
    - Debit in account A ≈ Credit in account B within ±3 days, 0.5% amount tolerance
    - Returns list of candidate pairs (not yet saved to DB).
    """
    txs = get_transactions(exclude_reconciled=False)

    # Only unreconciled transactions
    unreconciled = [
        t for t in txs
        if t.get("reconciliation_status") in ("unreconciled", None, "")
        and t.get("is_deleted") == 0
    ]

    # Get existing pairs to avoid re-proposing
    existing_pairs = get_reconciliation_pairs("transfer")
    existing_tx_ids = set()
    for p in existing_pairs:
        existing_tx_ids.add(p["tx_id_1"])
        existing_tx_ids.add(p["tx_id_2"])

    debits = [
        t for t in unreconciled
        if (t.get("debit") or 0) > 0 and t["id"] not in existing_tx_ids
    ]
    credits = [
        t for t in unreconciled
        if (t.get("credit") or 0) > 0 and t["id"] not in existing_tx_ids
    ]

    candidates: List[Dict[str, Any]] = []
    total = len(debits)

    for i, dt in enumerate(debits):
        if progress_callback:
            progress_callback(i / max(total, 1))

        debit_amount = dt["debit"]
        debit_date = dt["date"]
        debit_account = dt["account_name"]

        for ct in credits:
            # Must be different accounts
            if ct["account_name"] == debit_account:
                continue
            credit_amount = ct["credit"]
            credit_date = ct["date"]

            if not _amounts_match(debit_amount, credit_amount):
                continue
            if not _dates_within(debit_date, credit_date, TRANSFER_DAY_WINDOW):
                continue

            # Don't duplicate
            already = any(
                (c["tx_id_1"] == dt["id"] and c["tx_id_2"] == ct["id"]) or
                (c["tx_id_1"] == ct["id"] and c["tx_id_2"] == dt["id"])
                for c in candidates
            )
            if already:
                continue

            candidates.append({
                "tx_id_1": dt["id"],
                "tx_id_2": ct["id"],
                "tx_1_date": str(debit_date),
                "tx_2_date": str(credit_date),
                "tx_1_desc": dt["description"],
                "tx_2_desc": ct["description"],
                "tx_1_account": debit_account,
                "tx_2_account": ct["account_name"],
                "matched_amount": debit_amount,
                "type": "transfer",
                "status": "pending",
            })

    return candidates


def save_transfer_candidates(candidates: List[Dict[str, Any]]) -> int:
    """Persist candidate transfer pairs to DB. Returns count saved."""
    saved = 0
    for c in candidates:
        pair_id = upsert_reconciliation_pair({
            "tx_id_1": c["tx_id_1"],
            "tx_id_2": c["tx_id_2"],
            "matched_amount": c["matched_amount"],
            "match_date": c["tx_1_date"],
            "type": "transfer",
            "status": "transfer_pending",
        })
        if pair_id:
            saved += 1
    return saved


def approve_transfer(pair_id: str) -> None:
    """Approve a transfer pair and mark both transactions."""
    pairs = get_reconciliation_pairs("transfer")
    pair = next((p for p in pairs if p["id"] == pair_id), None)
    if not pair:
        return
    update_reconciliation_pair(pair_id, "transfer_approved")
    update_transaction(pair["tx_id_1"], {
        "reconciliation_status": "transfer_approved",
        "reconciliation_pair_id": pair_id,
    })
    update_transaction(pair["tx_id_2"], {
        "reconciliation_status": "transfer_approved",
        "reconciliation_pair_id": pair_id,
    })


def reject_transfer(pair_id: str) -> None:
    update_reconciliation_pair(pair_id, "transfer_rejected")


def bulk_approve_transfers(pair_ids: List[str]) -> None:
    for pid in pair_ids:
        approve_transfer(pid)


# ── B. Credit Card Payment Detection ──────────────────────────────────────────

def find_cc_payments(
    progress_callback=None,
) -> List[Dict[str, Any]]:
    """
    Find bank debits that match CC credits:
    - CC keyword in description OR account_type == 'credit_card' for credit side
    - Within ±5 days, 0.5% amount tolerance
    """
    txs = get_transactions(exclude_reconciled=False)
    unreconciled = [
        t for t in txs
        if t.get("reconciliation_status") in ("unreconciled", None, "")
        and t.get("is_deleted") == 0
    ]

    existing_pairs = get_reconciliation_pairs("cc_payment")
    existing_tx_ids = set()
    for p in existing_pairs:
        existing_tx_ids.add(p["tx_id_1"])
        existing_tx_ids.add(p["tx_id_2"])

    def is_cc_keyword(desc: str) -> bool:
        desc_u = desc.upper()
        return any(kw.upper() in desc_u for kw in CC_KEYWORDS)

    def is_cc_payment_credit(desc: str) -> bool:
        """True if CC credit description looks like a bill payment (not a refund)."""
        desc_u = desc.upper()
        return any(kw.upper() in desc_u for kw in CC_PAYMENT_CREDIT_KEYWORDS)

    # Bank debits that could be CC bill payments (CC keyword in desc OR any bank debit)
    bank_debits = [
        t for t in unreconciled
        if (t.get("debit") or 0) > 0
        and t["id"] not in existing_tx_ids
        and (is_cc_keyword(t.get("description", "")) or
             t.get("account_type") == "bank")
    ]

    # CC credits: from CC accounts (bill payments show as credits on CC statement)
    # Prefer credits that explicitly look like payments; fall back to all CC credits.
    cc_credits = [
        t for t in unreconciled
        if (t.get("credit") or 0) > 0
        and t["id"] not in existing_tx_ids
        and (
            t.get("account_type") == "credit_card"
            or is_cc_keyword(t.get("description", ""))
            or is_cc_payment_credit(t.get("description", ""))
        )
    ]

    candidates: List[Dict[str, Any]] = []
    total = len(bank_debits)

    for i, dt in enumerate(bank_debits):
        if progress_callback:
            progress_callback(i / max(total, 1))

        for ct in cc_credits:
            if ct["account_name"] == dt["account_name"]:
                continue
            if not _amounts_match(dt["debit"], ct["credit"]):
                continue
            if not _dates_within(dt["date"], ct["date"], CC_PAYMENT_DAY_WINDOW):
                continue

            already = any(
                (c["tx_id_1"] == dt["id"] and c["tx_id_2"] == ct["id"]) or
                (c["tx_id_1"] == ct["id"] and c["tx_id_2"] == dt["id"])
                for c in candidates
            )
            if already:
                continue

            candidates.append({
                "tx_id_1": dt["id"],
                "tx_id_2": ct["id"],
                "tx_1_date": str(dt["date"]),
                "tx_2_date": str(ct["date"]),
                "tx_1_desc": dt["description"],
                "tx_2_desc": ct["description"],
                "tx_1_account": dt["account_name"],
                "tx_2_account": ct["account_name"],
                "matched_amount": dt["debit"],
                "type": "cc_payment",
                "status": "pending",
            })

    return candidates


def save_cc_payment_candidates(candidates: List[Dict[str, Any]]) -> int:
    saved = 0
    for c in candidates:
        pair_id = upsert_reconciliation_pair({
            "tx_id_1": c["tx_id_1"],
            "tx_id_2": c["tx_id_2"],
            "matched_amount": c["matched_amount"],
            "match_date": c["tx_1_date"],
            "type": "cc_payment",
            "status": "cc_payment_pending",
        })
        if pair_id:
            saved += 1
    return saved


def approve_cc_payment(pair_id: str) -> None:
    pairs = get_reconciliation_pairs("cc_payment")
    pair = next((p for p in pairs if p["id"] == pair_id), None)
    if not pair:
        return
    update_reconciliation_pair(pair_id, "cc_payment_approved")
    update_transaction(pair["tx_id_1"], {
        "reconciliation_status": "cc_payment_approved",
        "reconciliation_pair_id": pair_id,
    })
    update_transaction(pair["tx_id_2"], {
        "reconciliation_status": "cc_payment_approved",
        "reconciliation_pair_id": pair_id,
    })


def reject_cc_payment(pair_id: str) -> None:
    update_reconciliation_pair(pair_id, "cc_payment_rejected")


# ── C. Personal Loan Matching ─────────────────────────────────────────────────

def tag_loan_given(tx_id: str, contact_name: str) -> str:
    """Tag an outgoing transaction as a loan given."""
    tag_id = create_loan_tag(tx_id, contact_name, "given")
    update_transaction(tx_id, {"reconciliation_status": "loan_given"})
    return tag_id


def find_loan_repayments(progress_callback=None) -> List[Dict[str, Any]]:
    """
    For each outstanding loan, look for incoming credits where description
    fuzzy-matches the contact name (score >= 80).
    Returns list of candidate matches.
    """
    outstanding_loans = get_loan_tags(status="outstanding")
    if not outstanding_loans:
        return []

    txs = get_transactions(exclude_reconciled=False)
    incoming_credits = [
        t for t in txs
        if (t.get("credit") or 0) > 0
        and t.get("reconciliation_status") not in (
            "transfer_approved", "cc_payment_approved", "loan_repaid"
        )
        and t.get("is_deleted") == 0
    ]

    candidates: List[Dict[str, Any]] = []
    total = len(outstanding_loans)

    for i, loan in enumerate(outstanding_loans):
        if progress_callback:
            progress_callback(i / max(total, 1))

        contact = loan["contact_name"]
        # Get the original loan transaction
        loan_txs = [t for t in txs if t["id"] == loan["tx_id"]]
        loan_tx = loan_txs[0] if loan_txs else None
        loan_date = loan_tx["date"] if loan_tx else None
        loan_amount = abs(loan_tx.get("debit") or 0) if loan_tx else None

        for ct in incoming_credits:
            # Must be after loan date
            if loan_date and ct["date"] <= loan_date:
                continue
            # Fuzzy match contact name in description
            score = fuzz.token_sort_ratio(
                contact.upper(),
                ct["description"].upper(),
            )
            if score >= LOAN_FUZZY_THRESHOLD:
                candidates.append({
                    "loan_tag_id": loan["id"],
                    "contact_name": contact,
                    "original_tx_id": loan["tx_id"],
                    "repayment_tx_id": ct["id"],
                    "repayment_date": str(ct["date"]),
                    "repayment_desc": ct["description"],
                    "repayment_amount": ct["credit"],
                    "original_amount": loan_amount,
                    "fuzzy_score": score,
                })

    return candidates


def approve_loan_repayment(
    loan_tag_id: str, repayment_tx_id: str
) -> None:
    """Mark loan as settled and tag repayment transaction."""
    update_loan_tag(loan_tag_id, {
        "linked_tx_id": repayment_tx_id,
        "status": "settled",
    })
    update_transaction(repayment_tx_id, {
        "reconciliation_status": "loan_repaid",
    })


# ── Summary ───────────────────────────────────────────────────────────────────

def get_reconciliation_summary() -> Dict[str, Any]:
    """Return counts for reconciliation summary panel."""
    transfers = get_reconciliation_pairs("transfer")
    cc_payments = get_reconciliation_pairs("cc_payment")
    loans = get_loan_tags()

    def count_by_status(pairs: List[Dict], prefix: str) -> Dict[str, int]:
        return {
            "pending": sum(1 for p in pairs if p["status"] == f"{prefix}_pending"),
            "approved": sum(1 for p in pairs if p["status"] == f"{prefix}_approved"),
            "rejected": sum(1 for p in pairs if p["status"] == f"{prefix}_rejected"),
        }

    transfer_counts = count_by_status(transfers, "transfer")
    cc_counts = count_by_status(cc_payments, "cc_payment")

    approved_transfers = [p for p in transfers if p["status"] == "transfer_approved"]
    approved_cc = [p for p in cc_payments if p["status"] == "cc_payment_approved"]

    total_reconciled = sum(p.get("matched_amount", 0) or 0 for p in approved_transfers + approved_cc)

    outstanding_loans = [lt for lt in loans if lt["status"] == "outstanding"]

    return {
        "transfers": transfer_counts,
        "cc_payments": cc_counts,
        "loans": {
            "outstanding": len(outstanding_loans),
            "settled": sum(1 for lt in loans if lt["status"] == "settled"),
        },
        "total_reconciled_amount": total_reconciled,
    }
