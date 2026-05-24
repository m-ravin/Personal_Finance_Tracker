"""
core/database.py
Full SQLAlchemy schema creation + CRUD helpers for Personal Finance Tracker.
Database is permanent (not wiped on restart). Located at data/finance.db.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Optional, List, Dict, Any

from sqlalchemy import (
    create_engine, text, Column, String, Float, Integer,
    DateTime, Date, MetaData, Table, inspect, event
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

# ── Path setup ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "finance.db"
DB_URL = f"sqlite:///{DB_PATH}"

# ── Engine ───────────────────────────────────────────────────────────────────
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

metadata = MetaData()

# ── Table definitions ─────────────────────────────────────────────────────────
transactions_table = Table(
    "transactions", metadata,
    Column("id", String, primary_key=True),
    Column("date", Date),
    Column("description", String),
    Column("debit", Float),
    Column("credit", Float),
    Column("net_amount", Float),          # credit - debit
    Column("balance", Float),
    Column("account_name", String),
    Column("account_type", String),       # 'bank' | 'credit_card'
    Column("source_file", String),
    Column("upload_ts", DateTime),
    Column("reconciliation_status", String),
    Column("reconciliation_pair_id", String),
    Column("category", String),
    Column("subcategory", String),
    Column("type_tag", String),
    Column("ai_confidence", Float),
    Column("final_category", String),     # user override
    Column("final_subcategory", String),
    Column("final_type_tag", String),
    Column("notes", String),
    Column("is_deleted", Integer, default=0),
)

reconciliation_pairs_table = Table(
    "reconciliation_pairs", metadata,
    Column("id", String, primary_key=True),
    Column("type", String),               # 'transfer' | 'cc_payment' | 'loan'
    Column("tx_id_1", String),
    Column("tx_id_2", String),
    Column("matched_amount", Float),
    Column("match_date", Date),
    Column("status", String),             # 'pending'|'approved'|'rejected'
    Column("created_ts", DateTime),
)

loan_tags_table = Table(
    "loan_tags", metadata,
    Column("id", String, primary_key=True),
    Column("tx_id", String),
    Column("contact_name", String),
    Column("direction", String),          # 'given' | 'repaid'
    Column("linked_tx_id", String),
    Column("status", String),             # 'outstanding' | 'settled'
    Column("created_ts", DateTime),
)

llm_settings_table = Table(
    "llm_settings", metadata,
    Column("id", Integer, primary_key=True),
    Column("provider", String),           # 'claude'|'openai'|'groq'|'none'
    Column("model", String),
    Column("api_key_hint", String),       # last 4 chars only
    Column("is_active", Integer, default=0),
    Column("updated_ts", DateTime),
)

column_mapping_profiles_table = Table(
    "column_mapping_profiles", metadata,
    Column("id", String, primary_key=True),
    Column("account_name", String, unique=True),
    Column("mapping_json", String),       # JSON string of col->field mapping
    Column("created_ts", DateTime),
    Column("updated_ts", DateTime),
)

budgets_table = Table(
    "budgets", metadata,
    Column("id", String, primary_key=True),
    Column("category", String),
    Column("monthly_budget", Float),
    Column("updated_ts", DateTime),
)


def init_db() -> None:
    """Create all tables if they don't exist."""
    metadata.create_all(engine)


def get_session() -> Session:
    """Return a new SQLAlchemy session."""
    return Session(engine)


# ── CRUD helpers ──────────────────────────────────────────────────────────────

def generate_id() -> str:
    return str(uuid.uuid4())


# ── Transactions ──────────────────────────────────────────────────────────────

def delete_by_source_file(source_file: str) -> int:
    """Soft-delete all non-deleted transactions from a given source file.
    Returns the number of rows marked deleted."""
    with get_session() as session:
        result = session.execute(
            text(
                "UPDATE transactions SET is_deleted=1 "
                "WHERE source_file=:sf AND is_deleted=0"
            ),
            {"sf": source_file},
        )
        session.commit()
        return result.rowcount


def upsert_transactions(rows: List[Dict[str, Any]]) -> int:
    """
    Insert transactions that don't already exist (dedup on date+description+amount).
    Uses a single bulk SELECT to fetch existing keys, then batch INSERT.
    Returns count of newly inserted rows.
    """
    if not rows:
        return 0

    with get_session() as session:
        # One query to fetch all existing dedup keys for the accounts in this batch
        accounts_in_batch = list({r.get("account_name") for r in rows if r.get("account_name")})
        if accounts_in_batch:
            placeholders = ",".join(f":a{i}" for i in range(len(accounts_in_batch)))
            existing_rows = session.execute(
                text(
                    f"SELECT date, description, "
                    f"round(coalesce(debit,0)+coalesce(credit,0),2), account_name "
                    f"FROM transactions WHERE account_name IN ({placeholders}) AND is_deleted=0"
                ),
                {f"a{i}": a for i, a in enumerate(accounts_in_batch)},
            ).fetchall()
            existing_keys = {
                (str(r[0]), str(r[1]), float(r[2]), str(r[3]))
                for r in existing_rows
            }
        else:
            existing_keys = set()

        to_insert = []
        now = datetime.utcnow()
        for row in rows:
            # Skip rows with invalid date or meaningless description
            row_date = row.get("date")
            row_desc = str(row.get("description") or "").strip()
            if not isinstance(row_date, date) or not row_desc or row_desc.lower() in ("nan", "nat", "none"):
                continue
            debit_val = row.get("debit") or 0.0
            credit_val = row.get("credit") or 0.0
            amount_key = round(debit_val + credit_val, 2)
            key = (str(row.get("date")), str(row.get("description")), float(amount_key), str(row.get("account_name")))
            if key in existing_keys:
                continue
            existing_keys.add(key)  # prevent intra-batch dupes
            row = dict(row)
            row.setdefault("id", generate_id())
            row.setdefault("upload_ts", now)
            row.setdefault("net_amount", (row.get("credit") or 0.0) - (row.get("debit") or 0.0))
            row.setdefault("reconciliation_status", "unreconciled")
            row.setdefault("is_deleted", 0)
            to_insert.append(row)

        if to_insert:
            session.execute(transactions_table.insert(), to_insert)
        session.commit()
    return len(to_insert)


def get_transactions(
    include_deleted: bool = False,
    account_name: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    exclude_reconciled: bool = False,
) -> List[Dict[str, Any]]:
    """Fetch transactions with optional filters. Returns list of dicts."""
    conditions = []
    params: Dict[str, Any] = {}

    if not include_deleted:
        conditions.append("is_deleted = 0")

    if account_name:
        conditions.append("account_name = :account_name")
        params["account_name"] = account_name

    if start_date:
        conditions.append("date >= :start_date")
        params["start_date"] = start_date

    if end_date:
        conditions.append("date <= :end_date")
        params["end_date"] = end_date

    if exclude_reconciled:
        conditions.append(
            "reconciliation_status NOT IN ('transfer_approved','cc_payment_approved')"
        )

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT * FROM transactions {where} ORDER BY date DESC"

    with get_session() as session:
        result = session.execute(text(sql), params)
        cols = result.keys()
        return [dict(zip(cols, row)) for row in result.fetchall()]


def update_transaction(tx_id: str, updates: Dict[str, Any]) -> None:
    """Update arbitrary fields on a transaction row."""
    set_parts = ", ".join(f"{k}=:{k}" for k in updates)
    updates["_id"] = tx_id
    with get_session() as session:
        session.execute(
            text(f"UPDATE transactions SET {set_parts} WHERE id=:_id"),
            updates,
        )
        session.commit()


def soft_delete_transactions(tx_ids: List[str]) -> None:
    if not tx_ids:
        return
    placeholders = ",".join(f":id{i}" for i in range(len(tx_ids)))
    params = {f"id{i}": v for i, v in enumerate(tx_ids)}
    with get_session() as session:
        session.execute(
            text(f"UPDATE transactions SET is_deleted=1 WHERE id IN ({placeholders})"),
            params,
        )
        session.commit()


def soft_delete_by_account(account_name: str) -> int:
    with get_session() as session:
        result = session.execute(
            text("UPDATE transactions SET is_deleted=1 WHERE account_name=:acc AND is_deleted=0"),
            {"acc": account_name},
        )
        session.commit()
        return result.rowcount


def soft_delete_by_date_range(start_date: date, end_date: date) -> int:
    with get_session() as session:
        result = session.execute(
            text(
                "UPDATE transactions SET is_deleted=1 "
                "WHERE date BETWEEN :s AND :e AND is_deleted=0"
            ),
            {"s": start_date, "e": end_date},
        )
        session.commit()
        return result.rowcount


def purge_deleted_transactions() -> int:
    with get_session() as session:
        result = session.execute(text("DELETE FROM transactions WHERE is_deleted=1"))
        session.commit()
        return result.rowcount


def soft_delete_all_transactions() -> int:
    with get_session() as session:
        result = session.execute(
            text("UPDATE transactions SET is_deleted=1 WHERE is_deleted=0")
        )
        session.commit()
        return result.rowcount


# ── DB stats ──────────────────────────────────────────────────────────────────

def get_db_stats() -> Dict[str, Any]:
    """Return quick summary stats for sidebar widget."""
    with get_session() as session:
        row = session.execute(
            text(
                "SELECT COUNT(*), MIN(date), MAX(date) "
                "FROM transactions WHERE is_deleted=0"
            )
        ).fetchone()
        count, min_date, max_date = row if row else (0, None, None)

        accounts = session.execute(
            text(
                "SELECT account_name, COUNT(*) as cnt "
                "FROM transactions WHERE is_deleted=0 "
                "GROUP BY account_name ORDER BY account_name"
            )
        ).fetchall()

    return {
        "total_transactions": count or 0,
        "min_date": min_date,
        "max_date": max_date,
        "accounts": [{"account_name": r[0], "count": r[1]} for r in accounts],
    }


def get_all_account_names() -> List[str]:
    with get_session() as session:
        rows = session.execute(
            text(
                "SELECT DISTINCT account_name FROM transactions "
                "WHERE is_deleted=0 ORDER BY account_name"
            )
        ).fetchall()
    return [r[0] for r in rows if r[0]]


# ── Reconciliation pairs ───────────────────────────────────────────────────────

def upsert_reconciliation_pair(pair: Dict[str, Any]) -> str:
    pair.setdefault("id", generate_id())
    pair.setdefault("created_ts", datetime.utcnow())
    # Ensure match_date is a Python date object
    if "match_date" in pair and isinstance(pair["match_date"], str):
        from datetime import datetime as _dt
        try:
            pair["match_date"] = _dt.strptime(pair["match_date"], "%Y-%m-%d").date()
        except ValueError:
            pair["match_date"] = None
    with get_session() as session:
        # Check if pair already exists by tx ids
        existing = session.execute(
            text(
                "SELECT id FROM reconciliation_pairs "
                "WHERE (tx_id_1=:t1 AND tx_id_2=:t2) "
                "OR (tx_id_1=:t2 AND tx_id_2=:t1)"
            ),
            {"t1": pair["tx_id_1"], "t2": pair["tx_id_2"]},
        ).fetchone()
        if existing:
            return existing[0]
        session.execute(reconciliation_pairs_table.insert().values(**pair))
        session.commit()
    return pair["id"]


def get_reconciliation_pairs(pair_type: Optional[str] = None) -> List[Dict[str, Any]]:
    where = "WHERE type=:t" if pair_type else ""
    params = {"t": pair_type} if pair_type else {}
    with get_session() as session:
        result = session.execute(
            text(f"SELECT * FROM reconciliation_pairs {where} ORDER BY created_ts DESC"),
            params,
        )
        cols = result.keys()
        return [dict(zip(cols, row)) for row in result.fetchall()]


def update_reconciliation_pair(pair_id: str, status: str) -> None:
    with get_session() as session:
        session.execute(
            text("UPDATE reconciliation_pairs SET status=:s WHERE id=:id"),
            {"s": status, "id": pair_id},
        )
        session.commit()


def delete_reconciliation_pairs_by_type(pair_type: str) -> None:
    with get_session() as session:
        session.execute(
            text("DELETE FROM reconciliation_pairs WHERE type=:t"),
            {"t": pair_type},
        )
        session.commit()


# ── Loan tags ──────────────────────────────────────────────────────────────────

def create_loan_tag(tx_id: str, contact_name: str, direction: str) -> str:
    tag_id = generate_id()
    with get_session() as session:
        session.execute(
            loan_tags_table.insert().values(
                id=tag_id,
                tx_id=tx_id,
                contact_name=contact_name,
                direction=direction,
                linked_tx_id=None,
                status="outstanding",
                created_ts=datetime.utcnow(),
            )
        )
        session.commit()
    return tag_id


def get_loan_tags(status: Optional[str] = None) -> List[Dict[str, Any]]:
    where = "WHERE status=:s" if status else ""
    params = {"s": status} if status else {}
    with get_session() as session:
        result = session.execute(
            text(f"SELECT * FROM loan_tags {where} ORDER BY created_ts DESC"),
            params,
        )
        cols = result.keys()
        return [dict(zip(cols, row)) for row in result.fetchall()]


def update_loan_tag(tag_id: str, updates: Dict[str, Any]) -> None:
    set_parts = ", ".join(f"{k}=:{k}" for k in updates)
    updates["_id"] = tag_id
    with get_session() as session:
        session.execute(
            text(f"UPDATE loan_tags SET {set_parts} WHERE id=:_id"),
            updates,
        )
        session.commit()


# ── LLM settings ──────────────────────────────────────────────────────────────

def get_active_llm_settings() -> Optional[Dict[str, Any]]:
    with get_session() as session:
        row = session.execute(
            text("SELECT * FROM llm_settings WHERE is_active=1 ORDER BY id DESC LIMIT 1")
        ).fetchone()
        if not row:
            return None
        cols = [
            "id", "provider", "model", "api_key_hint", "is_active", "updated_ts"
        ]
        return dict(zip(cols, row))


def save_llm_settings(provider: str, model: str, api_key_hint: str) -> None:
    with get_session() as session:
        session.execute(text("UPDATE llm_settings SET is_active=0"))
        existing = session.execute(
            text("SELECT id FROM llm_settings WHERE provider=:p AND model=:m"),
            {"p": provider, "m": model},
        ).fetchone()
        if existing:
            session.execute(
                text(
                    "UPDATE llm_settings SET is_active=1, api_key_hint=:hint, updated_ts=:ts "
                    "WHERE id=:id"
                ),
                {"hint": api_key_hint, "ts": datetime.utcnow(), "id": existing[0]},
            )
        else:
            session.execute(
                llm_settings_table.insert().values(
                    provider=provider,
                    model=model,
                    api_key_hint=api_key_hint,
                    is_active=1,
                    updated_ts=datetime.utcnow(),
                )
            )
        session.commit()


def disable_llm() -> None:
    with get_session() as session:
        session.execute(text("UPDATE llm_settings SET is_active=0"))
        session.commit()


# ── Column mapping profiles ────────────────────────────────────────────────────

def save_column_mapping(account_name: str, mapping: Dict[str, str]) -> None:
    import json
    now = datetime.utcnow()
    with get_session() as session:
        existing = session.execute(
            text("SELECT id FROM column_mapping_profiles WHERE account_name=:a"),
            {"a": account_name},
        ).fetchone()
        if existing:
            session.execute(
                text(
                    "UPDATE column_mapping_profiles SET mapping_json=:m, updated_ts=:t "
                    "WHERE account_name=:a"
                ),
                {"m": json.dumps(mapping), "t": now, "a": account_name},
            )
        else:
            session.execute(
                column_mapping_profiles_table.insert().values(
                    id=generate_id(),
                    account_name=account_name,
                    mapping_json=json.dumps(mapping),
                    created_ts=now,
                    updated_ts=now,
                )
            )
        session.commit()


def get_column_mapping(account_name: str) -> Optional[Dict[str, str]]:
    import json
    with get_session() as session:
        row = session.execute(
            text(
                "SELECT mapping_json FROM column_mapping_profiles WHERE account_name=:a"
            ),
            {"a": account_name},
        ).fetchone()
    if row:
        return json.loads(row[0])
    return None


def get_all_column_mappings() -> List[Dict[str, Any]]:
    import json
    with get_session() as session:
        rows = session.execute(
            text(
                "SELECT account_name, mapping_json, updated_ts "
                "FROM column_mapping_profiles ORDER BY account_name"
            )
        ).fetchall()
    return [
        {
            "account_name": r[0],
            "mapping": json.loads(r[1]),
            "updated_ts": r[2],
        }
        for r in rows
    ]


def delete_column_mapping(account_name: str) -> None:
    with get_session() as session:
        session.execute(
            text("DELETE FROM column_mapping_profiles WHERE account_name=:a"),
            {"a": account_name},
        )
        session.commit()


# ── Budgets ────────────────────────────────────────────────────────────────────

def get_budgets() -> Dict[str, float]:
    with get_session() as session:
        rows = session.execute(
            text("SELECT category, monthly_budget FROM budgets")
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def save_budget(category: str, monthly_budget: float) -> None:
    now = datetime.utcnow()
    with get_session() as session:
        existing = session.execute(
            text("SELECT id FROM budgets WHERE category=:c"), {"c": category}
        ).fetchone()
        if existing:
            session.execute(
                text(
                    "UPDATE budgets SET monthly_budget=:b, updated_ts=:t WHERE category=:c"
                ),
                {"b": monthly_budget, "t": now, "c": category},
            )
        else:
            session.execute(
                budgets_table.insert().values(
                    id=generate_id(),
                    category=category,
                    monthly_budget=monthly_budget,
                    updated_ts=now,
                )
            )
        session.commit()


def delete_budget(category: str) -> None:
    with get_session() as session:
        session.execute(
            text("DELETE FROM budgets WHERE category=:c"), {"c": category}
        )
        session.commit()


# ── Export helpers ─────────────────────────────────────────────────────────────

def get_transactions_for_export() -> List[Dict[str, Any]]:
    """All non-deleted transactions with effective category columns."""
    with get_session() as session:
        result = session.execute(
            text(
                """
                SELECT
                    date, description, debit, credit, net_amount,
                    account_name, account_type,
                    COALESCE(final_category, category) as category,
                    COALESCE(final_subcategory, subcategory) as subcategory,
                    COALESCE(final_type_tag, type_tag) as type_tag,
                    reconciliation_status, notes, source_file
                FROM transactions
                WHERE is_deleted=0
                ORDER BY date DESC
                """
            )
        )
        cols = result.keys()
        return [dict(zip(cols, row)) for row in result.fetchall()]


def get_monthly_summary() -> List[Dict[str, Any]]:
    with get_session() as session:
        result = session.execute(
            text(
                """
                SELECT
                    strftime('%Y-%m', date) as month,
                    COALESCE(final_category, category) as category,
                    SUM(net_amount) as total
                FROM transactions
                WHERE is_deleted=0
                  AND reconciliation_status NOT IN
                      ('transfer_approved','cc_payment_approved')
                GROUP BY month, category
                ORDER BY month, category
                """
            )
        )
        cols = result.keys()
        return [dict(zip(cols, row)) for row in result.fetchall()]


# Initialise on import
init_db()
