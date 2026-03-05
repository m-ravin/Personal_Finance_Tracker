"""
pages/1_Upload.py
Statement upload page.
- Accept CSV / XLSX / PDF
- Flexible column mapping with profile save/load
- Deduplication on insert
- Preview stats after upload
"""
import streamlit as st
import pandas as pd
from datetime import datetime

st.set_page_config(
    page_title="Upload Statements | Finance Tracker",
    page_icon="📤",
    layout="wide",
)

from core.database import (
    upsert_transactions,
    get_column_mapping,
    save_column_mapping,
    init_db,
)
from core.ingestion import (
    load_file,
    auto_detect_mapping,
    apply_mapping,
    validate_mapping,
    get_preview_stats,
    STANDARD_FIELDS,
)
from core.categorisation import categorise_batch
from core.ui_helpers import render_sidebar_stats

init_db()
render_sidebar_stats()

# ── Page ──────────────────────────────────────────────────────────────────────
st.title("📤 Upload Statements")
st.info(
    "Upload your bank or credit card statements (CSV, XLSX, or PDF). "
    "Map the columns to the standard fields — your mapping is saved per account for future uploads."
)

# ── Multi-file uploader ───────────────────────────────────────────────────────
uploaded_files = st.file_uploader(
    "Select one or more files",
    type=["csv", "xlsx", "xls", "pdf"],
    accept_multiple_files=True,
    help="Supported formats: CSV, Excel (XLSX/XLS), PDF",
)

if not uploaded_files:
    st.markdown("### No files selected yet.")
    st.markdown("""
    **Tips:**
    - Export your bank statement as CSV or Excel from your bank's website
    - Make sure the file has columns for date, description, and at least one amount column
    - PDF support requires the file to have selectable text (not scanned images)
    """)
    st.stop()

# ── Process each file ──────────────────────────────────────────────────────────
for uploaded_file in uploaded_files:
    st.markdown(f"---\n### 📄 {uploaded_file.name}")

    try:
        with st.spinner(f"Loading {uploaded_file.name}…"):
            file_bytes = uploaded_file.read()
            df = load_file(file_bytes, uploaded_file.name)
    except ValueError as e:
        st.error(f"❌ Failed to load file: {e}")
        continue
    except Exception as e:
        st.error(f"❌ Unexpected error loading {uploaded_file.name}: {e}")
        continue

    st.success(f"Loaded {len(df)} rows, {len(df.columns)} columns.")

    # ── Column preview ─────────────────────────────────────────────────────
    with st.expander("Raw data preview", expanded=False):
        st.dataframe(df.head(10), use_container_width=True)

    # ── Account info ───────────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        account_name = st.text_input(
            "Account Name",
            value=uploaded_file.name.rsplit(".", 1)[0],
            key=f"acc_name_{uploaded_file.name}",
            help="A unique name for this account (e.g. HDFC Savings, ICICI Credit Card)",
        )
    with col2:
        account_type = st.selectbox(
            "Account Type",
            options=["bank", "credit_card"],
            key=f"acc_type_{uploaded_file.name}",
        )

    # ── Column mapping ─────────────────────────────────────────────────────
    st.markdown("#### Column Mapping")

    saved_mapping = get_column_mapping(account_name) if account_name else None
    detected_mapping = auto_detect_mapping(list(df.columns))

    # Merge: start with saved mapping, supplement with newly auto-detected fields.
    # Special case for CC accounts: if transaction_type was not in saved mapping
    # (stale profile created before sign-column detection was added), force-correct it
    # even if the column was previously mis-assigned (e.g. BillingAmountSign → amount).
    _mapping_updated = False
    if saved_mapping:
        corrected_saved = dict(saved_mapping)
        saved_fields = set(corrected_saved.values())

        if account_type == "credit_card" and "transaction_type" not in saved_fields:
            tx_col = next(
                (col for col, field in detected_mapping.items() if field == "transaction_type"),
                None,
            )
            if tx_col:
                # Remove any wrong assignment for this column (e.g. mapped as 'amount')
                corrected_saved = {col: f for col, f in corrected_saved.items() if col != tx_col}
                corrected_saved[tx_col] = "transaction_type"
                _mapping_updated = True

        saved_fields = set(corrected_saved.values())
        extra_detected = {
            col: field for col, field in detected_mapping.items()
            if field not in saved_fields and col not in corrected_saved
        }
        if extra_detected or _mapping_updated:
            initial_mapping = {**corrected_saved, **extra_detected}
            if account_name:
                save_column_mapping(account_name, initial_mapping)
                _mapping_updated = True
        else:
            initial_mapping = corrected_saved
    else:
        initial_mapping = detected_mapping

    # Invert to {field_name: col_name} for form pre-population
    field_to_col_init = {v: k for k, v in initial_mapping.items()}

    missing_auto = validate_mapping(initial_mapping)
    can_auto = not missing_auto

    file_cols = ["(not mapped)"] + list(df.columns)
    field_labels = {
        "date": "📅 Date *",
        "description": "📝 Description *",
        "debit": "💸 Debit",
        "credit": "💰 Credit",
        "amount": "💳 Amount (single col)",
        "transaction_type": "🔄 Txn Type (CR/blank=Dr)",
        "balance": "🏦 Balance",
        "account_name": "👤 Account Name",
        "account_type": "🏷️ Account Type",
    }

    # ── Auto-import banner ──────────────────────────────────────────────────
    auto_submitted = False
    auto_mapping = {}

    if can_auto:
        if _mapping_updated:
            source = "saved profile (updated with new fields)"
        elif saved_mapping:
            source = "saved profile"
        else:
            source = "auto-detected"
        show_fields = ["date", "description", "debit", "credit", "amount", "transaction_type", "balance"]
        pairs = "  ·  ".join(
            f"`{field_to_col_init[f]}` → {field_labels.get(f, f).split(' ', 1)[1]}"
            for f in show_fields if f in field_to_col_init
        )
        st.success(f"✨ Column mapping {source} — {pairs}")

        # Warn if CC account but sign column not mapped
        if account_type == "credit_card" and "transaction_type" not in field_to_col_init and "debit" not in field_to_col_init:
            st.warning(
                "⚠️ CC statement detected but no transaction type column found (e.g. BillingAmountSign / CR/DR column). "
                "All transactions will be recorded as debits. "
                "Expand 'Configure columns manually' and map the sign column to **Txn Type**."
            )
        if st.button(
            "🚀 Quick Import" if not saved_mapping else "🚀 Import with saved mapping",
            key=f"auto_btn_{uploaded_file.name}",
            type="primary",
            use_container_width=True,
        ):
            auto_submitted = True
            auto_mapping = initial_mapping
            if account_name and not saved_mapping:
                save_column_mapping(account_name, initial_mapping)
                st.toast(f"Column mapping saved for {account_name}", icon="💾")
            elif _mapping_updated:
                st.toast(f"Saved mapping updated for {account_name} (added new fields)", icon="🔄")
    else:
        st.warning(f"⚠️ Could not auto-detect: {', '.join(missing_auto)}. Please map manually below.")

    # ── Manual mapping form (collapsed when auto is available) ──────────────
    mapping: dict = {}
    submitted = False

    with st.expander(
        "⚙️ Configure columns manually" if can_auto else "⚙️ Column Mapping (required)",
        expanded=not can_auto,
    ):
        with st.form(key=f"mapping_form_{uploaded_file.name}"):
            st.markdown("Map your file's columns to the standard fields:")
            form_cols = st.columns(4)

            for i, field in enumerate(STANDARD_FIELDS):
                with form_cols[i % 4]:
                    # Fixed: use inverted mapping {field → col} for correct pre-fill
                    current_col = field_to_col_init.get(field)
                    if current_col and current_col in df.columns:
                        default_idx = file_cols.index(current_col)
                    else:
                        default_idx = 0
                    selected = st.selectbox(
                        field_labels.get(field, field),
                        options=file_cols,
                        index=default_idx,
                        key=f"map_{uploaded_file.name}_{field}",
                    )
                    if selected != "(not mapped)":
                        mapping[selected] = field

            save_profile = st.checkbox(
                "💾 Save this mapping as profile for future uploads",
                value=True,
                key=f"save_profile_{uploaded_file.name}",
            )
            btn_col1, btn_col2 = st.columns([3, 1])
            submitted = btn_col1.form_submit_button("✅ Apply Mapping & Import", use_container_width=True)
            reset_mapping = btn_col2.form_submit_button(
                "🗑️ Reset saved mapping",
                use_container_width=True,
                help="Clear the saved profile for this account so mapping is re-detected from scratch",
            )

        if reset_mapping and account_name:
            from core.database import delete_column_mapping
            delete_column_mapping(account_name)
            st.toast(f"Saved mapping cleared for {account_name}. Reload to re-detect.", icon="🗑️")
            st.rerun()

    # ── Process import (shared by both paths) ───────────────────────────────
    if auto_submitted or submitted:
        use_mapping = auto_mapping if auto_submitted else mapping

        if not auto_submitted:
            missing = validate_mapping(use_mapping)
            if "date" in missing:
                st.error("❌ Date column is required.")
                continue
            if "description" in missing:
                st.error("❌ Description column is required.")
                continue
            if save_profile and account_name:
                save_column_mapping(account_name, use_mapping)
                st.toast(f"Column mapping saved for {account_name}", icon="💾")

        with st.spinner("Processing rows…"):
            valid_rows, failed_rows = apply_mapping(
                df, use_mapping, account_name, account_type, uploaded_file.name
            )

        if failed_rows:
            st.warning(f"⚠️ {len(failed_rows)} rows could not be parsed (skipped):")
            st.dataframe(
                pd.DataFrame([{"Row": r["row"], "Error": ", ".join(r["errors"])} for r in failed_rows[:20]]),
                use_container_width=True,
            )

        if not valid_rows:
            st.error("❌ No valid rows after applying mapping. Check your column assignments.")
            continue

        with st.spinner("Categorising transactions…"):
            valid_rows = categorise_batch(valid_rows, use_llm=False)

        with st.spinner("Saving to database…"):
            try:
                inserted = upsert_transactions(valid_rows)
            except Exception as e:
                st.error(f"❌ Database error: {e}")
                continue

        stats = get_preview_stats(valid_rows)
        st.success(f"✅ Imported **{inserted}** new transactions ({len(valid_rows) - inserted} duplicates skipped)")

        stat_cols = st.columns(5)
        stat_cols[0].metric("Total Rows", stats["count"])
        stat_cols[1].metric("Date From", str(stats["date_min"]) if stats["date_min"] else "—")
        stat_cols[2].metric("Date To", str(stats["date_max"]) if stats["date_max"] else "—")
        stat_cols[3].metric("Total Debit", f"₹{stats['total_debit']:,.2f}")
        stat_cols[4].metric("Total Credit", f"₹{stats['total_credit']:,.2f}")

        with st.expander("Preview imported transactions", expanded=True):
            preview_df = pd.DataFrame(valid_rows)[
                ["date", "description", "debit", "credit", "net_amount",
                 "account_name", "category", "subcategory"]
            ].head(50)
            st.dataframe(
                preview_df,
                column_config={
                    "date": st.column_config.DateColumn("Date"),
                    "debit": st.column_config.NumberColumn("Debit", format="₹%.2f"),
                    "credit": st.column_config.NumberColumn("Credit", format="₹%.2f"),
                    "net_amount": st.column_config.NumberColumn("Net", format="₹%.2f"),
                },
                use_container_width=True,
                hide_index=True,
            )

        # Auto-scan for transfer and CC payment pairs after each import
        with st.spinner("Checking for transfer and CC payment pairs…"):
            from core.reconciliation import (
                find_internal_transfers, save_transfer_candidates,
                find_cc_payments, save_cc_payment_candidates,
            )
            transfer_candidates = find_internal_transfers()
            new_transfer_pairs = save_transfer_candidates(transfer_candidates) if transfer_candidates else 0

            cc_candidates = find_cc_payments()
            new_cc_pairs = save_cc_payment_candidates(cc_candidates) if cc_candidates else 0

        msg_parts = []
        if new_transfer_pairs > 0:
            msg_parts.append(f"{new_transfer_pairs} transfer pair(s)")
        if new_cc_pairs > 0:
            msg_parts.append(f"{new_cc_pairs} CC payment pair(s)")
        if msg_parts:
            st.info(
                f"🔄 **{' and '.join(msg_parts)} detected** across your accounts. "
                "Go to **Reconcile** → approve them so they're excluded from Dashboard totals."
            )
        else:
            st.info("💡 Next step: Go to **Reconcile** to match transfers and CC payments.")
