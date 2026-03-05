"""
pages/2_Reconcile.py
Reconciliation page:
 A. Internal transfers
 B. Credit card payments
 C. Personal loans
"""
import streamlit as st
import pandas as pd
from datetime import datetime

st.set_page_config(
    page_title="Reconcile | Finance Tracker",
    page_icon="🔄",
    layout="wide",
)

from core.database import (
    get_reconciliation_pairs, get_loan_tags, get_transactions,
    update_transaction, init_db,
)
from core.reconciliation import (
    find_internal_transfers, save_transfer_candidates,
    approve_transfer, reject_transfer, bulk_approve_transfers,
    find_cc_payments, save_cc_payment_candidates,
    approve_cc_payment, reject_cc_payment,
    tag_loan_given, find_loan_repayments, approve_loan_repayment,
    get_reconciliation_summary,
)
from core.ui_helpers import render_sidebar_stats

init_db()
render_sidebar_stats()

# ── Page ──────────────────────────────────────────────────────────────────────
st.title("🔄 Reconciliation")
st.info(
    "Review auto-detected transfers and credit card payments. "
    "Approved pairs are excluded from expense calculations on the Dashboard. "
    "Use the Loans tab to track money lent to contacts."
)

# ── Summary panel ─────────────────────────────────────────────────────────────
summary = get_reconciliation_summary()

s_cols = st.columns(6)
s_cols[0].metric("Transfers Pending", summary["transfers"]["pending"])
s_cols[1].metric("Transfers Approved", summary["transfers"]["approved"])
s_cols[2].metric("CC Payments Pending", summary["cc_payments"]["pending"])
s_cols[3].metric("CC Payments Approved", summary["cc_payments"]["approved"])
s_cols[4].metric("Loans Outstanding", summary["loans"]["outstanding"])
s_cols[5].metric("Total Reconciled", f"₹{summary['total_reconciled_amount']:,.2f}")

st.markdown("---")

# ── Auto-scan on first load ───────────────────────────────────────────────────
if "reconcile_auto_scanned" not in st.session_state:
    with st.spinner("Auto-scanning for transfer and CC payment pairs…"):
        from core.reconciliation import (
            find_internal_transfers, save_transfer_candidates,
            find_cc_payments, save_cc_payment_candidates,
        )
        t_candidates = find_internal_transfers()
        t_saved = save_transfer_candidates(t_candidates) if t_candidates else 0
        cc_candidates = find_cc_payments()
        cc_saved = save_cc_payment_candidates(cc_candidates) if cc_candidates else 0
    st.session_state["reconcile_auto_scanned"] = True
    if t_saved + cc_saved > 0:
        st.toast(f"Auto-scan: {t_saved} transfer pair(s), {cc_saved} CC payment pair(s) queued.", icon="🔄")
        st.rerun()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["🔁 Internal Transfers", "💳 CC Payments", "🤝 Personal Loans"])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1: Internal Transfers
# ═══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.markdown("### Internal Transfer Detection")
    st.markdown(
        "Auto-detects debits in one account that match credits in another account "
        "within ±3 days and 0.5% amount tolerance."
    )

    # Load pending pairs
    all_pairs = get_reconciliation_pairs("transfer")
    pending_pairs = [p for p in all_pairs if p["status"] == "transfer_pending"]
    approved_pairs = [p for p in all_pairs if p["status"] == "transfer_approved"]
    rejected_pairs = [p for p in all_pairs if p["status"] == "transfer_rejected"]

    col_scan, col_approve_all, col_scan_approve = st.columns(3)

    with col_scan:
        if st.button("🔍 Scan for Transfers", key="scan_transfers", use_container_width=True):
            with st.spinner("Scanning transactions for transfer pairs…"):
                progress_bar = st.progress(0)
                candidates = find_internal_transfers(
                    progress_callback=lambda v: progress_bar.progress(min(v, 1.0))
                )
                progress_bar.empty()
                saved = save_transfer_candidates(candidates)
            st.toast(f"Found {len(candidates)} candidates, {saved} new pairs saved.", icon="✅")
            st.rerun()

    with col_approve_all:
        if pending_pairs:
            if st.button(
                f"✅ Approve All Pending ({len(pending_pairs)})",
                key="bulk_approve_transfers",
                type="primary",
                use_container_width=True,
            ):
                with st.spinner("Approving all transfer pairs…"):
                    bulk_approve_transfers([p["id"] for p in pending_pairs])
                st.toast(f"Approved {len(pending_pairs)} transfer pairs. Dashboard updated.", icon="✅")
                st.rerun()

    with col_scan_approve:
        if st.button(
            "🚀 Scan & Approve All",
            key="scan_approve_all",
            use_container_width=True,
            help="Scan for new transfer pairs and approve all pending in one click",
        ):
            with st.spinner("Scanning and approving all transfer pairs…"):
                candidates = find_internal_transfers()
                save_transfer_candidates(candidates)
                # Reload pending after scan
                fresh_pairs = get_reconciliation_pairs("transfer")
                fresh_pending = [p for p in fresh_pairs if p["status"] == "transfer_pending"]
                bulk_approve_transfers([p["id"] for p in fresh_pending])
            st.toast(f"Done: {len(fresh_pending)} pairs approved.", icon="✅")
            st.rerun()

    # Get transaction details for display
    tx_lookup: dict = {}
    all_txs = get_transactions()
    for t in all_txs:
        tx_lookup[t["id"]] = t

    def render_transfer_table(pairs: list, status: str, key_prefix: str) -> None:
        if not pairs:
            st.caption(f"No {status} pairs.")
            return

        for pair in pairs:
            tx1 = tx_lookup.get(pair["tx_id_1"], {})
            tx2 = tx_lookup.get(pair["tx_id_2"], {})

            with st.container(border=True):
                c1, c2, c3 = st.columns([3, 3, 1])
                with c1:
                    st.markdown(f"**Account A**: {tx1.get('account_name', '?')}")
                    st.caption(
                        f"📅 {tx1.get('date')}  |  "
                        f"📤 ₹{(tx1.get('debit') or 0):,.2f}  |  "
                        f"{tx1.get('description', '')[:50]}"
                    )
                with c2:
                    st.markdown(f"**Account B**: {tx2.get('account_name', '?')}")
                    st.caption(
                        f"📅 {tx2.get('date')}  |  "
                        f"📥 ₹{(tx2.get('credit') or 0):,.2f}  |  "
                        f"{tx2.get('description', '')[:50]}"
                    )
                with c3:
                    st.metric("Amount", f"₹{pair.get('matched_amount', 0):,.2f}")
                    if status == "pending":
                        approve_col, reject_col = st.columns(2)
                        if approve_col.button("✅", key=f"{key_prefix}_approve_{pair['id']}",
                                              help="Approve"):
                            approve_transfer(pair["id"])
                            st.rerun()
                        if reject_col.button("❌", key=f"{key_prefix}_reject_{pair['id']}",
                                             help="Reject"):
                            reject_transfer(pair["id"])
                            st.rerun()

    st.markdown(f"#### ⏳ Pending ({len(pending_pairs)})")
    render_transfer_table(pending_pairs, "pending", "tf_pend")

    with st.expander(f"✅ Approved ({len(approved_pairs)})", expanded=False):
        render_transfer_table(approved_pairs, "approved", "tf_appr")

    with st.expander(f"❌ Rejected ({len(rejected_pairs)})", expanded=False):
        render_transfer_table(rejected_pairs, "rejected", "tf_rej")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2: Credit Card Payments
# ═══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown("### Credit Card Payment Matching")
    st.markdown(
        "Matches bank debits with CC credits within ±5 days and 0.5% tolerance. "
        "Detects keywords: GPAY-CREDITCARD, CREDITCARD PAYMENT, CRED, NEFT CC, AUTOPAY CC."
    )

    cc_pairs = get_reconciliation_pairs("cc_payment")
    cc_pending = [p for p in cc_pairs if p["status"] == "cc_payment_pending"]

    cc_col1, cc_col2, cc_col3 = st.columns(3)
    with cc_col1:
        if st.button("🔍 Scan for CC Payments", key="scan_cc", use_container_width=True):
            with st.spinner("Scanning for CC payment pairs…"):
                progress_bar = st.progress(0)
                candidates = find_cc_payments(
                    progress_callback=lambda v: progress_bar.progress(min(v, 1.0))
                )
                progress_bar.empty()
                saved = save_cc_payment_candidates(candidates)
            st.toast(f"Found {len(candidates)} candidates, {saved} new pairs saved.", icon="✅")
            st.rerun()

    with cc_col2:
        if cc_pending:
            if st.button(
                f"✅ Approve All Pending ({len(cc_pending)})",
                key="bulk_approve_cc",
                type="primary",
                use_container_width=True,
            ):
                from core.reconciliation import approve_cc_payment
                for p in cc_pending:
                    approve_cc_payment(p["id"])
                st.toast(f"Approved {len(cc_pending)} CC payment pairs.", icon="✅")
                st.rerun()

    with cc_col3:
        if st.button(
            "🚀 Scan & Approve All",
            key="cc_scan_approve_all",
            use_container_width=True,
            help="Scan for CC payment pairs and approve all pending in one click",
        ):
            with st.spinner("Scanning and approving CC payment pairs…"):
                candidates = find_cc_payments()
                save_cc_payment_candidates(candidates)
                fresh_cc = get_reconciliation_pairs("cc_payment")
                fresh_pending_cc = [p for p in fresh_cc if p["status"] == "cc_payment_pending"]
                from core.reconciliation import approve_cc_payment
                for p in fresh_pending_cc:
                    approve_cc_payment(p["id"])
            st.toast(f"Done: {len(fresh_pending_cc)} CC pairs approved.", icon="✅")
            st.rerun()

    cc_approved = [p for p in cc_pairs if p["status"] == "cc_payment_approved"]
    cc_rejected = [p for p in cc_pairs if p["status"] == "cc_payment_rejected"]

    def render_cc_table(pairs: list, status: str, key_prefix: str) -> None:
        if not pairs:
            st.caption(f"No {status} CC payment pairs.")
            return
        for pair in pairs:
            tx1 = tx_lookup.get(pair["tx_id_1"], {})
            tx2 = tx_lookup.get(pair["tx_id_2"], {})
            with st.container(border=True):
                c1, c2, c3 = st.columns([3, 3, 1])
                with c1:
                    st.markdown(f"**Bank**: {tx1.get('account_name', '?')}")
                    st.caption(
                        f"📅 {tx1.get('date')}  |  "
                        f"📤 ₹{(tx1.get('debit') or 0):,.2f}  |  "
                        f"{tx1.get('description', '')[:50]}"
                    )
                with c2:
                    st.markdown(f"**CC**: {tx2.get('account_name', '?')}")
                    st.caption(
                        f"📅 {tx2.get('date')}  |  "
                        f"📥 ₹{(tx2.get('credit') or 0):,.2f}  |  "
                        f"{tx2.get('description', '')[:50]}"
                    )
                with c3:
                    st.metric("Amount", f"₹{pair.get('matched_amount', 0):,.2f}")
                    if status == "pending":
                        a_col, r_col = st.columns(2)
                        if a_col.button("✅", key=f"{key_prefix}_approve_{pair['id']}"):
                            approve_cc_payment(pair["id"])
                            st.rerun()
                        if r_col.button("❌", key=f"{key_prefix}_reject_{pair['id']}"):
                            reject_cc_payment(pair["id"])
                            st.rerun()

    st.markdown(f"#### ⏳ Pending ({len(cc_pending)})")
    render_cc_table(cc_pending, "pending", "cc_pend")

    with st.expander(f"✅ Approved ({len(cc_approved)})", expanded=False):
        render_cc_table(cc_approved, "approved", "cc_appr")

    with st.expander(f"❌ Rejected ({len(cc_rejected)})", expanded=False):
        render_cc_table(cc_rejected, "rejected", "cc_rej")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3: Personal Loans
# ═══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown("### Personal Loan Tracker")
    st.markdown(
        "Tag any outgoing transaction as a loan given to a contact. "
        "The system will watch for incoming credits from the same contact."
    )

    # ── Tag a transaction as loan ──────────────────────────────────────────
    st.markdown("#### Tag a Transaction as Loan Given")
    col_tag1, col_tag2 = st.columns([3, 1])

    with col_tag1:
        # Show recent debits for easy selection
        debits = [
            t for t in all_txs
            if (t.get("debit") or 0) > 0
            and t.get("reconciliation_status") not in (
                "transfer_approved", "cc_payment_approved", "loan_given"
            )
        ]
        debits_sorted = sorted(debits, key=lambda x: x.get("date") or "", reverse=True)[:100]

        debit_options = {
            f"{t['date']} | ₹{(t.get('debit') or 0):,.2f} | {t['description'][:60]}": t["id"]
            for t in debits_sorted
        }

        selected_tx_label = st.selectbox(
            "Select outgoing transaction",
            options=["— select —"] + list(debit_options.keys()),
            key="loan_tx_select",
        )

    with col_tag2:
        contact_name = st.text_input(
            "Contact Name",
            placeholder="e.g. John Doe",
            key="loan_contact_name",
        )

        if st.button("🤝 Tag as Loan", key="tag_loan", use_container_width=True):
            if selected_tx_label == "— select —":
                st.toast("Please select a transaction first.", icon="⚠️")
            elif not contact_name.strip():
                st.toast("Please enter the contact name.", icon="⚠️")
            else:
                tx_id = debit_options[selected_tx_label]
                tag_id = tag_loan_given(tx_id, contact_name.strip())
                st.toast(f"Tagged as loan given to {contact_name}!", icon="✅")
                st.rerun()

    st.markdown("---")

    # ── Scan for repayments ────────────────────────────────────────────────
    if st.button("🔍 Scan for Loan Repayments", key="scan_loans"):
        with st.spinner("Scanning for loan repayments…"):
            candidates = find_loan_repayments()
        if candidates:
            st.session_state["loan_candidates"] = candidates
            st.toast(f"Found {len(candidates)} potential repayments.", icon="✅")
        else:
            st.toast("No repayment matches found yet.", icon="ℹ️")

    if "loan_candidates" in st.session_state and st.session_state["loan_candidates"]:
        st.markdown("#### Potential Repayments Found")
        for cand in st.session_state["loan_candidates"]:
            with st.container(border=True):
                c1, c2, c3 = st.columns([3, 3, 1])
                with c1:
                    st.markdown(f"**Contact**: {cand['contact_name']}")
                    st.caption(f"Original: ₹{cand.get('original_amount', 0):,.2f}")
                with c2:
                    st.markdown(f"**Repayment**: {cand['repayment_desc'][:60]}")
                    st.caption(
                        f"📅 {cand['repayment_date']}  |  "
                        f"₹{(cand.get('repayment_amount') or 0):,.2f}  |  "
                        f"Match: {cand['fuzzy_score']}%"
                    )
                with c3:
                    if st.button(
                        "✅ Confirm",
                        key=f"confirm_repay_{cand['repayment_tx_id']}",
                    ):
                        approve_loan_repayment(
                            cand["loan_tag_id"], cand["repayment_tx_id"]
                        )
                        # Remove from candidates
                        st.session_state["loan_candidates"] = [
                            x for x in st.session_state["loan_candidates"]
                            if x["repayment_tx_id"] != cand["repayment_tx_id"]
                        ]
                        st.toast("Loan repayment confirmed!", icon="✅")
                        st.rerun()

    st.markdown("---")

    # ── Outstanding loans table ────────────────────────────────────────────
    st.markdown("#### Outstanding Loans")
    loan_tags = get_loan_tags(status="outstanding")

    if not loan_tags:
        st.caption("No outstanding loans.")
    else:
        loan_rows = []
        for lt in loan_tags:
            orig_tx = tx_lookup.get(lt["tx_id"], {})
            loan_rows.append({
                "Contact": lt["contact_name"],
                "Amount": orig_tx.get("debit") or 0,
                "Date Given": str(orig_tx.get("date") or "?"),
                "Description": orig_tx.get("description", "")[:60],
                "Days Outstanding": (
                    (datetime.utcnow().date() - orig_tx["date"]).days
                    if orig_tx.get("date") else "?"
                ),
                "Status": lt["status"],
            })

        loan_df = pd.DataFrame(loan_rows)
        st.dataframe(
            loan_df,
            column_config={
                "Amount": st.column_config.NumberColumn("Amount", format="₹%.2f"),
            },
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("Settled Loans", expanded=False):
        settled = get_loan_tags(status="settled")
        if not settled:
            st.caption("No settled loans.")
        else:
            settled_rows = []
            for lt in settled:
                orig_tx = tx_lookup.get(lt["tx_id"], {})
                settled_rows.append({
                    "Contact": lt["contact_name"],
                    "Amount": orig_tx.get("debit") or 0,
                    "Date Given": str(orig_tx.get("date") or "?"),
                    "Linked Repayment TX": lt.get("linked_tx_id", ""),
                })
            st.dataframe(pd.DataFrame(settled_rows), use_container_width=True, hide_index=True)
