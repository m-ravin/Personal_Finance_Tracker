"""
pages/3_Categorise.py
Categorisation page.
- Filter tabs: All | Needs Review | Uncategorised | Overridden
- Editable table: final_category, final_subcategory, type_tag, notes
- Bulk action: select rows → pick category → Apply
- Re-run categorisation button
- Add keyword rule button
"""
import streamlit as st
import pandas as pd
from typing import Dict, List

st.set_page_config(
    page_title="Categorise | Finance Tracker",
    page_icon="🏷️",
    layout="wide",
)

from core.database import (
    get_transactions, update_transaction, init_db,
)
from core.categorisation import (
    categorise_and_save, get_all_category_names,
    get_subcategories_for, add_keyword_rule, load_categories,
)
from core.ui_helpers import render_sidebar_stats, format_currency

init_db()
render_sidebar_stats()

st.title("🏷️ Categorise Transactions")
st.info(
    "Review and override transaction categories. "
    "Changes are saved immediately. "
    "Use 'Re-run Categorisation' to process uncategorised rows."
)

# ── Re-run button ─────────────────────────────────────────────────────────────
col_rerun, col_add_kw, _ = st.columns([1, 1, 2])

with col_rerun:
    if st.button("🔄 Re-run Categorisation", key="rerun_cat", use_container_width=True):
        with st.spinner("Running categorisation pipeline…"):
            progress_bar = st.progress(0)
            count = categorise_and_save(
                progress_callback=lambda v: progress_bar.progress(min(v, 1.0)),
                use_llm=True,
            )
            progress_bar.empty()
        st.toast(f"Categorised {count} transactions.", icon="✅")
        st.rerun()

# ── Add keyword rule ──────────────────────────────────────────────────────────
with col_add_kw:
    if st.button("➕ Add Keyword Rule", key="add_kw_btn", use_container_width=True):
        st.session_state["show_kw_form"] = not st.session_state.get("show_kw_form", False)

if st.session_state.get("show_kw_form", False):
    with st.form("add_keyword_form"):
        st.markdown("#### Add Keyword Rule")
        kw_col1, kw_col2 = st.columns(2)
        with kw_col1:
            new_keyword = st.text_input("Keyword (case-insensitive pattern)", key="new_kw")
            all_cats = get_all_category_names()
            kw_cat = st.selectbox("Category", options=all_cats, key="new_kw_cat")
        with kw_col2:
            kw_subs = get_subcategories_for(kw_cat) if kw_cat else []
            kw_sub = st.selectbox(
                "Subcategory", options=kw_subs + ["(new)"], key="new_kw_sub"
            )
            kw_new_sub = ""
            if kw_sub == "(new)":
                kw_new_sub = st.text_input("New Subcategory name", key="new_kw_sub_name")
            kw_tag = st.text_input("Type Tag", value="Misc", key="new_kw_tag")

        if st.form_submit_button("💾 Save Rule"):
            sub_final = kw_new_sub if kw_sub == "(new)" else kw_sub
            if not new_keyword.strip():
                st.toast("Please enter a keyword.", icon="⚠️")
            elif not kw_cat or not sub_final:
                st.toast("Please fill in category and subcategory.", icon="⚠️")
            else:
                add_keyword_rule(kw_cat, sub_final, kw_tag, new_keyword.strip())
                st.toast(f"Keyword '{new_keyword}' added to {kw_cat} > {sub_final}.", icon="✅")
                st.session_state["show_kw_form"] = False
                st.rerun()

# ── Load transactions ──────────────────────────────────────────────────────────
txs = get_transactions()

if not txs:
    st.warning("No transactions found. Upload some statements first.")
    st.stop()

df = pd.DataFrame(txs)

# Effective category display
def eff_cat(row):
    return row.get("final_category") or row.get("category") or "Uncategorised"
def eff_sub(row):
    return row.get("final_subcategory") or row.get("subcategory") or "Misc"
def eff_tag(row):
    return row.get("final_type_tag") or row.get("type_tag") or "Misc"

df["eff_category"] = df.apply(eff_cat, axis=1)
df["eff_subcategory"] = df.apply(eff_sub, axis=1)
df["eff_type_tag"] = df.apply(eff_tag, axis=1)
df["confidence_pct"] = (df["ai_confidence"].fillna(0) * 100).astype(int)

# ── Filter tabs ───────────────────────────────────────────────────────────────
filter_tabs = st.tabs(["All", "Needs Review (< 70%)", "Uncategorised", "Overridden"])

def display_df(filtered_df: pd.DataFrame, tab_key: str) -> None:
    if filtered_df.empty:
        st.caption("No transactions in this view.")
        return

    all_cats = [""] + get_all_category_names()

    # ── Bulk action ────────────────────────────────────────────────────────
    with st.expander("🔀 Bulk Category Assignment", expanded=False):
        bulk_cat = st.selectbox(
            "Assign category to selected rows",
            options=all_cats,
            key=f"bulk_cat_{tab_key}",
        )
        bulk_sub_opts = [""] + (get_subcategories_for(bulk_cat) if bulk_cat else [])
        bulk_sub = st.selectbox(
            "Subcategory",
            options=bulk_sub_opts,
            key=f"bulk_sub_{tab_key}",
        )

        # Show row selection
        selected_ids = st.multiselect(
            "Select transaction IDs to bulk-update",
            options=list(filtered_df["id"]),
            format_func=lambda x: filtered_df.set_index("id").loc[x, "description"][:60]
                if x in filtered_df.set_index("id").index else x,
            key=f"bulk_select_{tab_key}",
        )

        if st.button("Apply to Selected", key=f"bulk_apply_{tab_key}") and selected_ids and bulk_cat:
            for tx_id in selected_ids:
                update_transaction(tx_id, {
                    "final_category": bulk_cat,
                    "final_subcategory": bulk_sub or None,
                })
            st.toast(f"Updated {len(selected_ids)} transactions.", icon="✅")
            st.rerun()

    # ── Editable table ─────────────────────────────────────────────────────
    display_cols = [
        "id", "date", "description", "net_amount", "account_name",
        "eff_category", "eff_subcategory", "eff_type_tag",
        "final_category", "final_subcategory", "final_type_tag",
        "notes", "confidence_pct",
    ]
    # Keep only columns that exist
    display_cols = [c for c in display_cols if c in filtered_df.columns]
    edit_df = filtered_df[display_cols].copy()
    edit_df["date"] = pd.to_datetime(edit_df["date"], errors="coerce")

    cat_list = get_all_category_names()

    edited = st.data_editor(
        edit_df,
        column_config={
            "id": st.column_config.TextColumn("ID", disabled=True, width="small"),
            "date": st.column_config.DateColumn("Date", disabled=True),
            "description": st.column_config.TextColumn("Description", disabled=True, width="large"),
            "net_amount": st.column_config.NumberColumn("Amount", format="₹%.2f", disabled=True),
            "account_name": st.column_config.TextColumn("Account", disabled=True),
            "eff_category": st.column_config.TextColumn("Auto Category", disabled=True),
            "eff_subcategory": st.column_config.TextColumn("Auto Sub", disabled=True),
            "eff_type_tag": st.column_config.TextColumn("Type Tag", disabled=True),
            "final_category": st.column_config.SelectboxColumn(
                "Override Category",
                options=[""] + cat_list,
                required=False,
            ),
            "final_subcategory": st.column_config.TextColumn("Override Sub"),
            "final_type_tag": st.column_config.TextColumn("Override Tag"),
            "notes": st.column_config.TextColumn("Notes", width="medium"),
            "confidence_pct": st.column_config.ProgressColumn(
                "Confidence", min_value=0, max_value=100, format="%d%%"
            ),
        },
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key=f"data_editor_{tab_key}",
    )

    # Persist any changes
    if edited is not None:
        orig_indexed = edit_df.set_index("id")
        edited_indexed = edited.set_index("id")

        changed_count = 0
        for tx_id in edited_indexed.index:
            if tx_id not in orig_indexed.index:
                continue
            orig_row = orig_indexed.loc[tx_id]
            edit_row = edited_indexed.loc[tx_id]

            updates = {}
            for field in ["final_category", "final_subcategory", "final_type_tag", "notes"]:
                if field not in edit_row.index:
                    continue
                orig_val = orig_row.get(field, "") or ""
                edit_val = edit_row.get(field, "") or ""
                if str(edit_val) != str(orig_val):
                    updates[field] = edit_val if edit_val else None

            if updates:
                update_transaction(tx_id, updates)
                changed_count += 1

        if changed_count > 0:
            st.toast(f"Saved {changed_count} changes.", icon="💾")


# Tab 1: All
with filter_tabs[0]:
    display_df(df, "all")

# Tab 2: Needs Review
with filter_tabs[1]:
    needs_review = df[
        (df["ai_confidence"].fillna(0) < 0.7) &
        (df["final_category"].isna() | (df["final_category"] == ""))
    ]
    display_df(needs_review, "review")

# Tab 3: Uncategorised
with filter_tabs[2]:
    uncategorised = df[
        df["eff_category"].isin(["Uncategorised", "", None]) |
        df["eff_category"].isna()
    ]
    display_df(uncategorised, "uncat")

# Tab 4: Overridden
with filter_tabs[3]:
    overridden = df[
        df["final_category"].notna() & (df["final_category"] != "")
    ]
    display_df(overridden, "overridden")
