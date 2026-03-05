"""
app.py
Entry point for Personal Finance Tracker.
Sets up navigation and shared sidebar components.
"""
import streamlit as st

# Page config must be the very first Streamlit call
st.set_page_config(
    page_title="Personal Finance Tracker",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Ensure DB tables exist on startup
from core.database import init_db
init_db()

from core.ui_helpers import render_sidebar_stats

# ── Sidebar navigation ────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("# 💰 Finance Tracker")
    st.markdown("---")
    st.page_link("app.py", label="🏠 Home", icon="🏠")
    st.page_link("pages/1_Upload.py", label="Upload Statements")
    st.page_link("pages/2_Reconcile.py", label="Reconcile")
    st.page_link("pages/3_Categorise.py", label="Categorise")
    st.page_link("pages/4_Dashboard.py", label="Dashboard")
    st.page_link("pages/5_Settings.py", label="Settings")

# Sidebar stats widget
render_sidebar_stats()

# ── Home page ─────────────────────────────────────────────────────────────────
st.title("💰 Personal Finance Tracker")

st.info(
    "Welcome! Use the sidebar to navigate between pages. "
    "Start by uploading your bank or credit card statements on the **Upload** page."
)

col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("""
    ### 1️⃣ Upload Statements
    Upload CSV, XLSX, or PDF files from any bank.
    Map your columns once — the profile is saved for next time.
    """)
    st.page_link("pages/1_Upload.py", label="Go to Upload →")

with col2:
    st.markdown("""
    ### 2️⃣ Reconcile
    Auto-detect internal transfers, credit card payments,
    and track personal loans. Approve or reject each match.
    """)
    st.page_link("pages/2_Reconcile.py", label="Go to Reconcile →")

with col3:
    st.markdown("""
    ### 3️⃣ Categorise
    Keyword → fuzzy → AI-powered categorisation.
    Override any transaction with one click.
    """)
    st.page_link("pages/3_Categorise.py", label="Go to Categorise →")

col4, col5, col6 = st.columns(3)

with col4:
    st.markdown("""
    ### 4️⃣ Dashboard
    Treemaps, waterfall charts, monthly trends,
    KPI bar, and smart insights — all in one view.
    """)
    st.page_link("pages/4_Dashboard.py", label="Go to Dashboard →")

with col5:
    st.markdown("""
    ### 5️⃣ Settings
    Configure LLM provider, set monthly budgets,
    manage categories, and control your data.
    """)
    st.page_link("pages/5_Settings.py", label="Go to Settings →")

with col6:
    st.markdown("""
    ### 📥 Quick Export
    Use the **Export** buttons in the sidebar to download
    transactions, monthly summary, or reconciliation report.
    """)

st.markdown("---")
st.caption(
    "Personal Finance Tracker · Built with Streamlit + SQLite · "
    "Data stored locally in `data/finance.db`"
)
