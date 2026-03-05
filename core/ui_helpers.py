"""
core/ui_helpers.py
Shared UI helpers: sidebar stats widget, export buttons, etc.
Imported by every page.
"""
from __future__ import annotations

import io
from typing import Any, Dict

import pandas as pd
import streamlit as st

from core.database import get_db_stats, get_transactions_for_export, get_monthly_summary
from core.database import get_reconciliation_pairs


def render_sidebar_stats() -> None:
    """Render DB statistics and quick-export buttons in the sidebar."""
    stats = get_db_stats()

    with st.sidebar:
        st.markdown("---")
        st.markdown("### 📊 Database")
        col1, col2 = st.columns(2)
        col1.metric("Transactions", f"{stats['total_transactions']:,}")
        if stats["min_date"] and stats["max_date"]:
            col2.metric("Date Range", f"{stats['min_date']} →")
            st.caption(f"↳ {stats['max_date']}")
        else:
            col2.metric("Date Range", "—")

        if stats["accounts"]:
            with st.expander("Accounts", expanded=False):
                for acc in stats["accounts"]:
                    st.caption(f"• {acc['account_name']}: {acc['count']:,} txns")

        st.markdown("[🗑 Manage Data](/5_Settings)", unsafe_allow_html=True)
        st.markdown("---")

        # Export buttons
        st.markdown("### 📥 Export")
        _export_all_button()
        _export_monthly_button()
        _export_reconciliation_button()


def _export_all_button() -> None:
    if st.button("All Transactions", key="sidebar_export_all", use_container_width=True):
        with st.spinner("Preparing export…"):
            rows = get_transactions_for_export()
            if not rows:
                st.toast("No transactions to export.", icon="ℹ️")
                return
            df = pd.DataFrame(rows)
            buf = io.BytesIO()
            df.to_excel(buf, index=False)
            buf.seek(0)
            st.download_button(
                "📥 Download All Transactions",
                data=buf,
                file_name="all_transactions.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_all_txns",
            )


def _export_monthly_button() -> None:
    if st.button("Monthly Summary", key="sidebar_export_monthly", use_container_width=True):
        with st.spinner("Preparing monthly summary…"):
            rows = get_monthly_summary()
            if not rows:
                st.toast("No data for monthly summary.", icon="ℹ️")
                return
            df = pd.DataFrame(rows)
            pivot = df.pivot_table(
                index="category", columns="month", values="total", aggfunc="sum"
            ).fillna(0)
            pivot["TOTAL"] = pivot.sum(axis=1)
            totals_row = pivot.sum(axis=0)
            totals_row.name = "TOTAL"
            pivot = pd.concat([pivot, totals_row.to_frame().T])

            buf = io.BytesIO()
            pivot.to_excel(buf)
            buf.seek(0)
            st.download_button(
                "📥 Download Monthly Summary",
                data=buf,
                file_name="monthly_summary.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_monthly",
            )


def _export_reconciliation_button() -> None:
    if st.button("Reconciliation Report", key="sidebar_export_recon", use_container_width=True):
        with st.spinner("Preparing reconciliation report…"):
            transfers = get_reconciliation_pairs("transfer")
            cc_payments = get_reconciliation_pairs("cc_payment")
            all_pairs = transfers + cc_payments
            if not all_pairs:
                st.toast("No reconciliation pairs found.", icon="ℹ️")
                return
            df = pd.DataFrame(all_pairs)
            buf = io.BytesIO()
            df.to_excel(buf, index=False)
            buf.seek(0)
            st.download_button(
                "📥 Download Reconciliation Report",
                data=buf,
                file_name="reconciliation_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_recon",
            )


def format_currency(amount: float, symbol: str = "₹") -> str:
    if amount is None:
        return "—"
    return f"{symbol}{amount:,.2f}"


def confidence_badge(confidence: float) -> str:
    """Return colored badge HTML for confidence value."""
    if confidence >= 0.9:
        color = "#7cb47c"
    elif confidence >= 0.7:
        color = "#d4a843"
    elif confidence >= 0.5:
        color = "#d4889a"
    else:
        color = "#888888"
    pct = int(confidence * 100)
    return f'<span style="background:{color};color:#fff;padding:2px 6px;border-radius:4px;font-size:0.8em">{pct}%</span>'
