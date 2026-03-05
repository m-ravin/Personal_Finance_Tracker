"""
core/ingestion.py
File parsers (CSV, XLSX, PDF) with flexible column mapping.
"""
from __future__ import annotations

import io
import re
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ── Date parsing ──────────────────────────────────────────────────────────────
DATE_FORMATS = [
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%m/%d/%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%Y/%m/%d",
    "%b %d, %Y",
    "%d-%b-%Y",
    "%d-%b-%y",
]


def try_parse_date(val: Any) -> Optional[date]:
    """Try multiple date formats; return None if all fail."""
    if val is None:
        return None
    if isinstance(val, (date, datetime)):
        return val.date() if isinstance(val, datetime) else val
    s = str(val).strip()
    if not s or s.lower() in ("nan", "nat", "none", "-", "n/a", ""):
        return None
    # pandas may give Timestamp
    try:
        ts = pd.to_datetime(s, dayfirst=True)
        if pd.isnull(ts):
            return None
        return ts.date()
    except Exception:
        pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(val: Any) -> Optional[float]:
    """Convert amount string like '1,234.56' or '(100.00)' to float."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val) if not pd.isna(val) else None
    s = str(val).strip().replace(",", "").replace(" ", "")
    if not s or s in ("-", "—", ""):
        return None
    # Negative in parentheses: (100.00)
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


# ── Raw file loaders ──────────────────────────────────────────────────────────

def load_csv(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """Load CSV with fallback encodings."""
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), encoding=enc, dtype=str)
            df.columns = df.columns.str.strip()
            return df
        except Exception:
            continue
    raise ValueError(f"Could not parse CSV file: {filename}")


def load_xlsx(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """Load first sheet of XLSX."""
    try:
        df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)
        df.columns = df.columns.str.strip()
        return df
    except Exception as e:
        raise ValueError(f"Could not parse XLSX file: {filename} — {e}")


def load_pdf(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """
    Extract tables from PDF using pdfplumber.
    Returns first plausible table as DataFrame.
    """
    try:
        import pdfplumber
    except ImportError:
        raise ValueError("pdfplumber not installed.")

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            all_rows: List[List[str]] = []
            header: Optional[List[str]] = None
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table:
                        continue
                    if header is None and table:
                        header = [str(c).strip() if c else f"col_{i}"
                                  for i, c in enumerate(table[0])]
                        all_rows.extend(table[1:])
                    else:
                        all_rows.extend(table)

            if not all_rows or header is None:
                # Try extract_text as fallback
                raise ValueError(
                    "No tables found in PDF. Please convert to CSV/XLSX manually."
                )

            df = pd.DataFrame(all_rows, columns=header, dtype=str)
            # Drop fully empty rows
            df = df.dropna(how="all")
            df.columns = df.columns.str.strip()
            return df
    except Exception as e:
        raise ValueError(f"PDF parsing failed for {filename}: {e}")


def load_file(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """Dispatch to correct loader based on file extension."""
    ext = Path(filename).suffix.lower()
    if ext == ".csv":
        return load_csv(file_bytes, filename)
    elif ext in (".xlsx", ".xls"):
        return load_xlsx(file_bytes, filename)
    elif ext == ".pdf":
        return load_pdf(file_bytes, filename)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


# ── Column mapping ─────────────────────────────────────────────────────────────

STANDARD_FIELDS = [
    "date", "description", "debit", "credit", "amount", "transaction_type",
    "balance", "account_name", "account_type",
]

OPTIONAL_FIELDS = {"balance", "account_name", "account_type", "amount", "transaction_type"}

REQUIRED_FIELDS = set(STANDARD_FIELDS) - OPTIONAL_FIELDS


def auto_detect_mapping(columns: List[str]) -> Dict[str, str]:
    """
    Heuristic auto-detection of column→field mapping.
    Returns a dict like {"Date": "date", "Narration": "description", ...}
    Handles common Indian bank statement formats.
    """
    col_lower = {c: c.lower().strip() for c in columns}
    mapping: Dict[str, str] = {}

    # Priority-ordered patterns: first match wins per field.
    # IMPORTANT: transaction_type must come before amount so that
    # "BillingAmountSign" is claimed before "billing amount" patterns run.
    patterns = {
        "date": [
            "transaction date", "tran date", "txn date", "value date",
            "value dat", "posting date", "date",
        ],
        "description": [
            "transaction details", "transaction remarks", "txn description",
            "transaction description", "narration", "particulars",
            "description", "details", "remarks", "reference",
        ],
        "debit": [
            "debit amount", "withdrawal amount(inr)", "withdrawal amount",
            "amount dr", "amount (dr)", "dr amount", "dr",
            "debit",
        ],
        "credit": [
            "credit amount", "deposit amount(inr)", "deposit amount",
            "amount cr", "amount (cr)", "cr amount", "cr",
            "credit",
        ],
        # transaction_type before amount: ensures BillingAmountSign is claimed
        # before "billing amount" substring patterns run on the amount field.
        "transaction_type": [
            "billingamountsign",   # HDFC CC: no spaces
            "billing amount sign", # with spaces
            "amount sign",         # suffix match
            "amountsign",          # no-space variant
            "billing sign",
            "txnsign", "cr/dr", "dr/cr", "crdr", "drcr",
            "txn type", "transaction type", "debit/credit",
        ],
        # balance before amount so "balance amount" is claimed by balance first
        "balance": [
            "closing balance", "balance(inr)", "balance (inr)",
            "running balance", "available balance", "bal", "balance",
        ],
        "amount": [
            "amount(in rs)", "amount (in rs)", "transaction amount",
            "txn amount",
            # "billing amount" safe here because BillingAmountSign is already
            # claimed by transaction_type above (already-mapped cols are skipped)
            "billing amount",
            "amount",  # plain "Amount" column (common in CC exports)
            "amt",     # common abbreviation
        ],
        "account_name": ["account name", "account number", "account"],
        "account_type": ["account type"],
    }

    for field, keywords in patterns.items():
        for kw in keywords:
            for col, col_l in col_lower.items():
                if col in mapping:
                    continue
                if col_l == kw or kw in col_l:
                    mapping[col] = field
                    break
            if field in mapping.values():
                break

    return mapping


def apply_mapping(
    df: pd.DataFrame,
    mapping: Dict[str, str],
    account_name: str,
    account_type: str,
    source_file: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Apply column mapping to raw DataFrame.
    Returns (valid_rows, failed_rows).
    valid_rows are ready for DB insertion.
    """
    # Reverse mapping: field → original column
    field_to_col = {v: k for k, v in mapping.items()}

    # CC heuristic: scan unmapped columns for a sign column (CR/DR/blank values).
    # Triggers when: amount is mapped but transaction_type is not, OR the
    # account is credit_card and has no separate debit/credit columns mapped.
    _needs_sign_scan = "transaction_type" not in field_to_col and (
        "amount" in field_to_col
        or (
            account_type == "credit_card"
            and "debit" not in field_to_col
            and "credit" not in field_to_col
        )
    )
    if _needs_sign_scan:
        mapped_cols = set(field_to_col.values())
        _sign_values = {"CR", "DR", "CREDIT", "DEBIT", "C", "D"}
        for _col in df.columns:
            if _col in mapped_cols:
                continue
            _vals = df[_col].dropna().astype(str).str.strip().str.upper()
            _vals = _vals[_vals != ""]
            if len(_vals) > 0 and set(_vals.unique()).issubset(_sign_values):
                field_to_col["transaction_type"] = _col
                break

    valid_rows: List[Dict[str, Any]] = []
    failed_rows: List[Dict[str, Any]] = []

    for idx, row in df.iterrows():
        errors = []

        # Date
        date_val = None
        if "date" in field_to_col:
            date_val = try_parse_date(row.get(field_to_col["date"]))
        if not isinstance(date_val, date):
            errors.append("Could not parse date")

        # Description
        desc = ""
        if "description" in field_to_col:
            desc = str(row.get(field_to_col["description"]) or "").strip()
        if not desc:
            errors.append("Empty description")

        # Amounts
        debit = None
        credit = None
        if "debit" in field_to_col:
            debit = parse_amount(row.get(field_to_col["debit"]))
        if "credit" in field_to_col:
            credit = parse_amount(row.get(field_to_col["credit"]))

        # Single-amount column + transaction_type column
        # (e.g. CC statements: Amount(in Rs) + BillingAmountSign = "CR" / blank)
        if debit is None and credit is None and "amount" in field_to_col:
            amt = parse_amount(row.get(field_to_col["amount"]))
            if amt is not None:
                amt = abs(amt)
                tx_type = ""
                if "transaction_type" in field_to_col:
                    _raw = row.get(field_to_col["transaction_type"])
                    # Explicitly handle NaN (float) and nan/none strings
                    if _raw is None or (isinstance(_raw, float) and pd.isna(_raw)):
                        tx_type = ""
                    else:
                        tx_type = str(_raw).strip().upper()
                        if tx_type in ("NAN", "NONE", "N/A", "NA", "-", ""):
                            tx_type = ""
                if tx_type in ("CR", "CREDIT", "C"):
                    credit = amt
                elif tx_type in ("DR", "DEBIT", "D"):
                    debit = amt
                else:
                    # Blank or unknown = debit (CC convention: purchases have no sign)
                    debit = amt

        # Fallback: unmapped column whose name looks like an amount column.
        # Checks transaction_type (BillingAmountSign) first for CC statements;
        # falls back to +/- sign for bank single-column formats.
        if debit is None and credit is None:
            _amount_names = {
                "amount", "amt", "transaction amount", "txn amount",
                "billing amount", "amount(in rs)", "amount (in rs)",
            }
            for col in df.columns:
                col_l = col.lower().strip()
                if col_l not in _amount_names and not (
                    "amount" in col_l
                    and "sign" not in col_l
                    and "balance" not in col_l
                ):
                    continue
                amt = parse_amount(row.get(col))
                if amt is not None:
                    # Check transaction_type first (CC: blank=debit, CR=credit)
                    _ft = ""
                    if "transaction_type" in field_to_col:
                        _r = row.get(field_to_col["transaction_type"])
                        if _r is not None and not (isinstance(_r, float) and pd.isna(_r)):
                            _ft = str(_r).strip().upper()
                            if _ft in ("NAN", "NONE", "N/A", "NA", "-", ""):
                                _ft = ""
                    if _ft in ("CR", "CREDIT", "C"):
                        credit = abs(amt)
                    elif _ft in ("DR", "DEBIT", "D"):
                        debit = abs(amt)
                    elif amt < 0:
                        # No sign column: use +/- (bank single-column convention)
                        debit = abs(amt)
                    else:
                        # Positive + no sign column: CC → debit, bank → credit
                        if account_type == "credit_card":
                            debit = abs(amt)
                        else:
                            credit = amt
                break

        balance = None
        if "balance" in field_to_col:
            balance = parse_amount(row.get(field_to_col["balance"]))

        # Account override from file if provided
        acc_name = account_name
        if "account_name" in field_to_col:
            mapped_acc = str(row.get(field_to_col["account_name"]) or "").strip()
            if mapped_acc:
                acc_name = mapped_acc

        acc_type = account_type
        if "account_type" in field_to_col:
            mapped_type = str(row.get(field_to_col["account_type"]) or "").strip().lower()
            if mapped_type in ("bank", "credit_card"):
                acc_type = mapped_type

        if errors:
            failed_rows.append({"row": idx + 1, "data": dict(row), "errors": errors})
            continue

        valid_rows.append({
            "date": date_val,
            "description": desc,
            "debit": debit,
            "credit": credit,
            "net_amount": (credit or 0.0) - (debit or 0.0),
            "balance": balance,
            "account_name": acc_name,
            "account_type": acc_type,
            "source_file": source_file,
        })

    return valid_rows, failed_rows


def validate_mapping(mapping: Dict[str, str]) -> List[str]:
    """Return list of missing required fields."""
    mapped_fields = set(mapping.values())
    missing = REQUIRED_FIELDS - mapped_fields
    # Need at least one amount column: debit, credit, or amount (single-col CC format)
    if "debit" not in mapped_fields and "credit" not in mapped_fields and "amount" not in mapped_fields:
        missing.add("debit, credit, or amount")
    return sorted(missing)


def get_preview_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute preview statistics for uploaded data."""
    if not rows:
        return {"count": 0}

    dates = [r["date"] for r in rows if r.get("date")]
    debits = [r["debit"] or 0.0 for r in rows]
    credits = [r["credit"] or 0.0 for r in rows]

    return {
        "count": len(rows),
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "total_debit": sum(debits),
        "total_credit": sum(credits),
    }
