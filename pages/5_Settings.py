"""
pages/5_Settings.py
Settings page:
1. LLM Configuration
2. Data Management (soft/hard delete, backup/restore)
3. Category Management (view/edit categories.json)
4. Budget Settings
5. Column Mapping Profiles
"""
import io
import json
import os
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st
from dotenv import set_key, load_dotenv

st.set_page_config(
    page_title="Settings | Finance Tracker",
    page_icon="⚙️",
    layout="wide",
)

from core.database import (
    DB_PATH,
    init_db,
    get_active_llm_settings,
    save_llm_settings,
    disable_llm,
    get_all_account_names,
    get_db_stats,
    soft_delete_all_transactions,
    soft_delete_by_account,
    soft_delete_by_date_range,
    purge_deleted_transactions,
    get_all_column_mappings,
    delete_column_mapping,
    save_column_mapping,
    get_budgets,
    save_budget,
    delete_budget,
)
from core.categorisation import (
    load_categories, save_categories,
    get_all_category_names, get_subcategories_for,
)
from core.llm import test_llm_connection, PROVIDER_MODELS
from core.ui_helpers import render_sidebar_stats

init_db()
render_sidebar_stats()

st.title("⚙️ Settings")
st.info("Configure LLM, manage your data, set budgets, and customise categories.")

tabs = st.tabs([
    "🤖 LLM Config",
    "🗑 Data Management",
    "🏷️ Categories",
    "💰 Budgets",
    "📋 Column Mappings",
])

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_FILE)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: LLM Configuration
# ══════════════════════════════════════════════════════════════════════════════
with tabs[0]:
    st.markdown("### LLM Provider Configuration")
    st.markdown(
        "LLM is **optional**. If disabled, categorisation uses keyword + fuzzy matching only. "
        "API keys are stored only in your session and optionally in `.env`. "
        "Only the last 4 characters are shown in the DB."
    )

    active = get_active_llm_settings()
    current_provider = active["provider"] if active else "none"

    provider = st.radio(
        "Provider",
        options=["none", "claude", "openai", "groq"],
        format_func=lambda x: {
            "none": "❌ Disabled",
            "claude": "🟣 Anthropic Claude",
            "openai": "🟢 OpenAI",
            "groq": "🔵 Groq (free tier)",
        }[x],
        index=["none", "claude", "openai", "groq"].index(current_provider),
        horizontal=True,
        key="llm_provider_radio",
    )

    if provider != "none":
        col_api, col_model = st.columns(2)

        with col_api:
            # Pre-fill from env
            env_key_map = {
                "claude": "ANTHROPIC_API_KEY",
                "openai": "OPENAI_API_KEY",
                "groq": "GROQ_API_KEY",
            }
            env_key = os.environ.get(env_key_map.get(provider, ""), "")
            session_key = st.session_state.get(f"{provider}_api_key", "")
            placeholder = f"••••{env_key[-4:]}" if env_key else "Enter API key…"

            api_key_input = st.text_input(
                "API Key",
                type="password",
                placeholder=placeholder,
                value="",
                key=f"api_key_input_{provider}",
                help="Leave blank to use the key already in your .env file or session.",
            )

            save_to_env = st.checkbox(
                "Save to .env file for future sessions",
                value=False,
                key="save_to_env_cb",
            )

        with col_model:
            models = PROVIDER_MODELS.get(provider, [])
            default_model = active["model"] if active and active["provider"] == provider else models[0]
            default_idx = models.index(default_model) if default_model in models else 0
            selected_model = st.selectbox(
                "Model",
                options=models,
                index=default_idx,
                key="llm_model_select",
            )

        col_save, col_test, col_disable = st.columns(3)

        with col_save:
            if st.button("💾 Save Settings", key="save_llm", use_container_width=True):
                # Resolve actual key
                actual_key = api_key_input.strip() or env_key or session_key
                if not actual_key:
                    st.toast("Please enter an API key.", icon="⚠️")
                else:
                    # Store in session
                    st.session_state[f"{provider}_api_key"] = actual_key
                    # Optionally save to .env
                    if save_to_env:
                        env_var = env_key_map[provider]
                        set_key(str(ENV_FILE), env_var, actual_key)
                        st.toast(f"Saved {env_var} to .env", icon="💾")
                    # Save hint to DB
                    hint = actual_key[-4:] if len(actual_key) >= 4 else "****"
                    save_llm_settings(provider, selected_model, hint)
                    st.toast(f"LLM set to {provider} / {selected_model}", icon="✅")
                    st.rerun()

        with col_test:
            if st.button("🧪 Test Connection", key="test_llm", use_container_width=True):
                actual_key = api_key_input.strip() or env_key or session_key
                if not actual_key:
                    st.toast("Enter an API key first.", icon="⚠️")
                else:
                    st.session_state[f"{provider}_api_key"] = actual_key
                    with st.spinner(f"Testing {provider}…"):
                        result = test_llm_connection(provider, actual_key, selected_model)
                    if result["success"]:
                        st.success(
                            f"✅ Connected! Latency: {result['latency_ms']}ms\n\n"
                            f"Test result: {json.dumps(result['result'], indent=2)}"
                        )
                    else:
                        st.error(f"❌ Connection failed: {result['error']}")

        with col_disable:
            if st.button("🚫 Disable LLM", key="disable_llm", use_container_width=True):
                disable_llm()
                st.toast("LLM disabled. Falling back to keyword/fuzzy matching.", icon="ℹ️")
                st.rerun()

    else:
        st.info("LLM is disabled. Categorisation will use keyword + fuzzy matching only.")
        if active and active["provider"] != "none":
            if st.button("Disable active LLM", key="disable_llm_none"):
                disable_llm()
                st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2: Data Management
# ══════════════════════════════════════════════════════════════════════════════
with tabs[1]:
    st.markdown("### Data Management")

    stats = get_db_stats()
    col_s1, col_s2, col_s3 = st.columns(3)
    col_s1.metric("Total Transactions", f"{stats['total_transactions']:,}")
    col_s2.metric("Oldest", str(stats["min_date"] or "—"))
    col_s3.metric("Newest", str(stats["max_date"] or "—"))

    if stats["accounts"]:
        acc_df = pd.DataFrame(stats["accounts"])
        st.dataframe(acc_df, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("#### 🗑 Delete Data (Soft Delete)")
    st.markdown("Soft-deleted records are hidden but not permanently removed until you purge them.")

    del_tab1, del_tab2, del_tab3 = st.tabs([
        "Delete by Account", "Delete by Date Range", "Delete All"
    ])

    with del_tab1:
        accounts = get_all_account_names()
        if not accounts:
            st.caption("No accounts in database.")
        else:
            del_account = st.selectbox("Select Account", options=accounts, key="del_account_select")
            del_confirm_acc = st.text_input(
                f'Type the account name to confirm: "{del_account}"',
                key="del_confirm_acc",
            )
            if st.button("🗑 Delete Account Data", key="del_by_account"):
                if del_confirm_acc == del_account:
                    with st.spinner("Soft-deleting…"):
                        count = soft_delete_by_account(del_account)
                    st.toast(f"Soft-deleted {count} transactions from {del_account}.", icon="✅")
                    st.rerun()
                else:
                    st.toast("Account name does not match. Please retype exactly.", icon="⚠️")

    with del_tab2:
        del_col1, del_col2 = st.columns(2)
        with del_col1:
            del_start = st.date_input("From Date", key="del_start_date",
                                       value=stats["min_date"] or date.today())
        with del_col2:
            del_end = st.date_input("To Date", key="del_end_date",
                                     value=stats["max_date"] or date.today())
        del_confirm_range = st.text_input(
            'Type "DELETE" to confirm date range deletion', key="del_confirm_range"
        )
        if st.button("🗑 Delete Date Range", key="del_by_range"):
            if del_confirm_range == "DELETE":
                with st.spinner("Soft-deleting…"):
                    count = soft_delete_by_date_range(del_start, del_end)
                st.toast(f"Soft-deleted {count} transactions.", icon="✅")
                st.rerun()
            else:
                st.toast("Type DELETE to confirm.", icon="⚠️")

    with del_tab3:
        del_all_confirm = st.text_input(
            'Type "DELETE" to delete ALL transactions', key="del_all_confirm"
        )
        if st.button("🗑 Delete ALL Data", key="del_all", type="primary"):
            if del_all_confirm == "DELETE":
                with st.spinner("Soft-deleting all…"):
                    count = soft_delete_all_transactions()
                st.toast(f"Soft-deleted {count} transactions.", icon="✅")
                st.rerun()
            else:
                st.toast("Type DELETE to confirm.", icon="⚠️")

    st.markdown("---")
    st.markdown("#### ☠️ Permanently Purge Deleted Records")
    purge_confirm = st.text_input(
        'Type "PURGE" to permanently delete soft-deleted records', key="purge_confirm"
    )
    if st.button("🔥 Purge Deleted Records", key="purge_btn", type="primary"):
        if purge_confirm == "PURGE":
            count = purge_deleted_transactions()
            st.toast(f"Permanently deleted {count} records.", icon="✅")
            st.rerun()
        else:
            st.toast("Type PURGE to confirm.", icon="⚠️")

    st.markdown("---")
    st.markdown("#### 📦 Backup & Restore")

    col_backup, col_restore = st.columns(2)
    with col_backup:
        if st.button("💾 Download DB Backup", use_container_width=True):
            if DB_PATH.exists():
                with open(DB_PATH, "rb") as f:
                    db_bytes = f.read()
                st.download_button(
                    "📥 Download finance.db",
                    data=db_bytes,
                    file_name=f"finance_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
                    mime="application/octet-stream",
                    key="dl_db_backup",
                )
            else:
                st.toast("No database file found.", icon="⚠️")

    with col_restore:
        restore_file = st.file_uploader(
            "Restore from backup (.db file)",
            type=["db"],
            key="restore_db_uploader",
        )
        if restore_file and st.button("📂 Restore Database", use_container_width=True):
            restore_confirm = st.text_input(
                'Type "RESTORE" to overwrite current database', key="restore_confirm"
            )
            if restore_confirm == "RESTORE":
                backup_path = DB_PATH.parent / f"finance_pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
                if DB_PATH.exists():
                    shutil.copy(DB_PATH, backup_path)
                with open(DB_PATH, "wb") as f:
                    f.write(restore_file.read())
                init_db()
                st.toast("Database restored. Previous DB backed up locally.", icon="✅")
                st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3: Category Management
# ══════════════════════════════════════════════════════════════════════════════
with tabs[2]:
    st.markdown("### Category Management")
    st.markdown("View and edit the category tree used for auto-categorisation.")

    cats = load_categories()

    # View as flat table
    flat_rows = []
    for cat in cats:
        for sub in cat.get("subcategories", []):
            flat_rows.append({
                "Category": cat["category"],
                "Subcategory": sub["subcategory"],
                "Type Tag": sub.get("type_tag", ""),
                "Keywords": ", ".join(sub.get("keywords", [])),
                "Keyword Count": len(sub.get("keywords", [])),
            })
    flat_df = pd.DataFrame(flat_rows)

    with st.expander("📋 Full Category Table", expanded=True):
        st.dataframe(flat_df, use_container_width=True, hide_index=True)

    # Export categories
    if st.button("📥 Export as CSV", key="export_cats_csv"):
        csv = flat_df.to_csv(index=False)
        st.download_button(
            "Download categories.csv",
            data=csv,
            file_name="categories.csv",
            mime="text/csv",
            key="dl_cats_csv",
        )

    st.markdown("---")
    st.markdown("#### ➕ Add New Category or Subcategory")

    with st.form("add_cat_form"):
        new_cat_name = st.text_input("Category Name")
        new_sub_name = st.text_input("Subcategory Name")
        new_type_tag = st.text_input("Type Tag", value="Misc")
        new_keywords = st.text_area(
            "Keywords (one per line or comma-separated)",
            placeholder="AMAZON\nSHOPPING\nDELIVERY",
        )

        if st.form_submit_button("💾 Add"):
            if not new_cat_name or not new_sub_name:
                st.toast("Category and subcategory are required.", icon="⚠️")
            else:
                kw_list = [k.strip() for k in new_keywords.replace(",", "\n").split("\n") if k.strip()]
                # Find or create category
                found = False
                for cat in cats:
                    if cat["category"].lower() == new_cat_name.lower():
                        cat["subcategories"].append({
                            "subcategory": new_sub_name,
                            "type_tag": new_type_tag,
                            "keywords": kw_list,
                        })
                        found = True
                        break
                if not found:
                    cats.append({
                        "category": new_cat_name,
                        "subcategories": [{
                            "subcategory": new_sub_name,
                            "type_tag": new_type_tag,
                            "keywords": kw_list,
                        }],
                    })
                save_categories(cats)
                st.toast(f"Added {new_cat_name} > {new_sub_name}", icon="✅")
                st.rerun()

    st.markdown("---")
    st.markdown("#### 🔀 Merge Subcategories")
    st.caption("Move all keywords from one subcategory into another, then delete the source.")

    with st.form("merge_cats_form"):
        all_cat_names = [c["category"] for c in cats]
        src_cat = st.selectbox("Source Category", all_cat_names, key="merge_src_cat")
        src_subs = [s["subcategory"] for c in cats if c["category"] == src_cat for s in c.get("subcategories", [])]
        src_sub = st.selectbox("Source Subcategory", src_subs, key="merge_src_sub")

        tgt_cat = st.selectbox("Target Category", all_cat_names, key="merge_tgt_cat")
        tgt_subs = [s["subcategory"] for c in cats if c["category"] == tgt_cat for s in c.get("subcategories", [])]
        tgt_sub = st.selectbox("Target Subcategory", tgt_subs, key="merge_tgt_sub")

        if st.form_submit_button("🔀 Merge"):
            # Find source keywords
            src_keywords = []
            for cat in cats:
                if cat["category"] == src_cat:
                    for sub in cat["subcategories"]:
                        if sub["subcategory"] == src_sub:
                            src_keywords = sub["keywords"]
                            sub["keywords"] = []  # Clear source
                            break

            # Add to target
            for cat in cats:
                if cat["category"] == tgt_cat:
                    for sub in cat["subcategories"]:
                        if sub["subcategory"] == tgt_sub:
                            sub["keywords"] = list(set(sub["keywords"] + src_keywords))
                            break

            save_categories(cats)
            st.toast(f"Merged {src_sub} → {tgt_sub}", icon="✅")
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4: Budget Settings
# ══════════════════════════════════════════════════════════════════════════════
with tabs[3]:
    st.markdown("### Monthly Budget Settings")
    st.markdown(
        "Set monthly spending budgets per category. "
        "Budget vs Actual progress bars appear in the Dashboard Insights panel."
    )

    budgets = get_budgets()
    all_cats = get_all_category_names()

    # Render budget inputs as a grid
    st.markdown("#### Current Budgets")

    budget_cols = st.columns(3)
    updated_budgets: Dict[str, float] = {}

    for i, cat in enumerate(all_cats):
        with budget_cols[i % 3]:
            current = budgets.get(cat, 0.0)
            val = st.number_input(
                cat,
                min_value=0.0,
                value=float(current),
                step=500.0,
                format="%.0f",
                key=f"budget_input_{cat}",
            )
            updated_budgets[cat] = val

    if st.button("💾 Save All Budgets", key="save_budgets", use_container_width=True):
        with st.spinner("Saving budgets…"):
            for cat, budget in updated_budgets.items():
                if budget > 0:
                    save_budget(cat, budget)
                else:
                    delete_budget(cat)
        st.toast("Budgets saved!", icon="✅")
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5: Column Mapping Profiles
# ══════════════════════════════════════════════════════════════════════════════
with tabs[4]:
    st.markdown("### Column Mapping Profiles")
    st.markdown(
        "Saved column mapping profiles. "
        "When you upload a file for an account with a saved profile, "
        "the mapping is applied automatically."
    )

    profiles = get_all_column_mappings()
    if not profiles:
        st.caption("No saved profiles yet. Upload a file and save the mapping.")
    else:
        for profile in profiles:
            with st.container(border=True):
                col_p1, col_p2 = st.columns([3, 1])
                with col_p1:
                    st.markdown(f"**{profile['account_name']}**")
                    st.caption(f"Last updated: {profile['updated_ts']}")
                    # Show mapping
                    mapping_display = pd.DataFrame(
                        [{"File Column": k, "Standard Field": v}
                         for k, v in profile["mapping"].items()]
                    )
                    st.dataframe(mapping_display, use_container_width=True, hide_index=True)

                with col_p2:
                    # Rename
                    new_name = st.text_input(
                        "Rename account",
                        value=profile["account_name"],
                        key=f"rename_{profile['account_name']}",
                    )
                    if st.button("💾 Rename", key=f"save_rename_{profile['account_name']}"):
                        if new_name != profile["account_name"] and new_name.strip():
                            old_mapping = profile["mapping"]
                            delete_column_mapping(profile["account_name"])
                            save_column_mapping(new_name.strip(), old_mapping)
                            st.toast(f"Renamed to {new_name}", icon="✅")
                            st.rerun()

                    if st.button(
                        "🗑 Delete Profile",
                        key=f"del_profile_{profile['account_name']}",
                        type="secondary",
                    ):
                        delete_column_mapping(profile["account_name"])
                        st.toast(f"Deleted profile for {profile['account_name']}", icon="✅")
                        st.rerun()
