"""
pages/4_Dashboard.py
Full analytics dashboard with Plotly charts, KPI bar, and insights panel.
Supports drill-through filtering: click any chart label to filter transactions.
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

st.set_page_config(
    page_title="Dashboard | Finance Tracker",
    page_icon="📊",
    layout="wide",
)

from core.database import get_transactions, get_loan_tags, get_budgets, init_db
from core.categorisation import get_effective_category
from core.ui_helpers import render_sidebar_stats

init_db()
render_sidebar_stats()

# ── Colour palette ─────────────────────────────────────────────────────────────
PALETTE = [
    "#7cb47c", "#8b6fba", "#d4a843", "#d4889a", "#4e8bc4",
    "#e07b4a", "#5dbfbf", "#b07eb0", "#a4c74a", "#e0c45c",
]

# ── Drill-through state ────────────────────────────────────────────────────────
if "drill" not in st.session_state:
    st.session_state["drill"] = {}


def _set_drill(**kwargs) -> None:
    st.session_state["drill"] = {k: v for k, v in kwargs.items() if v is not None}


def _clear_drill() -> None:
    st.session_state["drill"] = {}


# ── Helper ─────────────────────────────────────────────────────────────────────
def ensure_date(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return pd.to_datetime(val).date()
    except Exception:
        return None


def build_df(
    txs: List[Dict],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    accounts: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
    exclude_reconciled: bool = True,
    show_mode: str = "expenses",  # "all" | "expenses" | "income"
    months: Optional[List[str]] = None,  # e.g. ["2024-01", "2024-03"]
) -> pd.DataFrame:
    if not txs:
        return pd.DataFrame()

    df = pd.DataFrame(txs)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])

    if start_date:
        df = df[df["date"].dt.date >= start_date]
    if end_date:
        df = df[df["date"].dt.date <= end_date]
    if accounts:
        df = df[df["account_name"].isin(accounts)]

    # Effective category — vectorized (much faster than row-wise apply)
    df["eff_category"] = (
        df["final_category"].replace("", None).fillna(
            df["category"].replace("", None)
        ).fillna("Uncategorised")
    )
    df["eff_subcategory"] = (
        df["final_subcategory"].replace("", None).fillna(
            df["subcategory"].replace("", None)
        ).fillna("Misc")
    )

    if categories:
        df = df[df["eff_category"].isin(categories)]

    if exclude_reconciled:
        df = df[~df["reconciliation_status"].isin(
            ["transfer_approved", "cc_payment_approved"]
        )]

    if show_mode == "expenses":
        df = df[df["net_amount"] < 0]
    elif show_mode == "income":
        df = df[df["net_amount"] > 0]

    df["month"] = df["date"].dt.to_period("M").astype(str)
    if months:
        df = df[df["month"].isin(months)]
    df["month_dt"] = df["date"].dt.to_period("M").dt.to_timestamp()
    df["abs_amount"] = df["net_amount"].abs()
    return df


# ── Page start ─────────────────────────────────────────────────────────────────
st.title("📊 Dashboard")
st.info("Use the sidebar filters to slice your data. Click any chart label to drill into transactions.")

# ── Load data ──────────────────────────────────────────────────────────────────
all_txs = get_transactions()

if not all_txs:
    st.warning("No transactions found. Upload some statements on the Upload page.")
    st.stop()

all_df_raw = pd.DataFrame(all_txs)
all_df_raw["date"] = pd.to_datetime(all_df_raw["date"], errors="coerce")
all_df_raw = all_df_raw.dropna(subset=["date"])

# ── Sidebar filters ────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🔍 Filters")

    min_date = all_df_raw["date"].min().date()
    max_date = all_df_raw["date"].max().date()
    default_start = date(max_date.year, 1, 1)

    date_range = st.date_input(
        "Date Range",
        value=(default_start, max_date),
        min_value=min_date,
        max_value=max_date,
        key="dash_date_range",
    )
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_date, end_date = date_range[0], date_range[1]
    else:
        start_date, end_date = default_start, max_date

    all_accounts = sorted(all_df_raw["account_name"].dropna().unique().tolist())
    sel_accounts = st.multiselect(
        "Accounts", options=all_accounts, default=all_accounts, key="dash_accounts"
    )

    all_cats_raw = (
        all_df_raw["final_category"].replace("", None)
        .fillna(all_df_raw["category"].replace("", None))
        .fillna("Uncategorised")
        .unique()
    )
    all_cats = sorted(all_cats_raw.tolist())
    sel_cats = st.multiselect(
        "Categories", options=all_cats, default=all_cats, key="dash_cats"
    )

    # Month selector (derived from data within the date range)
    months_in_range = sorted(set(
        all_df_raw[
            (all_df_raw["date"].dt.date >= start_date) &
            (all_df_raw["date"].dt.date <= end_date)
        ]["date"].dt.to_period("M").astype(str).tolist()
    ))
    sel_months = st.multiselect(
        "Months",
        options=months_in_range,
        default=[],
        key="dash_months",
        help="Pick specific months. Leave empty to include all months in the date range.",
    )

    excl_reconciled = st.toggle("Exclude reconciled transactions", value=True, key="dash_excl_recon")
    show_mode = st.radio(
        "Show", ["expenses", "income", "all"], index=0, key="dash_show_mode",
        format_func=lambda x: {"expenses": "Expenses only", "income": "Income only", "all": "All"}[x]
    )

    st.markdown("---")
    if st.session_state["drill"]:
        if st.button("✕ Clear drill-through filter", use_container_width=True):
            _clear_drill()
            st.rerun()


# Build filtered df
df = build_df(
    all_txs,
    start_date=start_date,
    end_date=end_date,
    accounts=sel_accounts or None,
    categories=sel_cats or None,
    exclude_reconciled=excl_reconciled,
    show_mode="all",  # We use all for income/expense split below
    months=sel_months or None,
)

if df.empty:
    st.warning("No transactions match the current filters.")
    st.stop()

expense_df = df[df["net_amount"] < 0].copy()
income_df = df[df["net_amount"] > 0].copy()

if show_mode == "expenses":
    chart_df = expense_df
elif show_mode == "income":
    chart_df = income_df
else:
    chart_df = df

# ── Prior period (for delta calculations) ────────────────────────────────────
period_days = (end_date - start_date).days
prior_start = start_date - timedelta(days=period_days)
prior_end = start_date - timedelta(days=1)

prior_df = build_df(
    all_txs,
    start_date=prior_start,
    end_date=prior_end,
    exclude_reconciled=excl_reconciled,
    show_mode="all",
    # No month filter on prior period — always compare full prior window
)
prior_expense = prior_df[prior_df["net_amount"] < 0]["net_amount"].abs().sum()

# ── KPI functions ──────────────────────────────────────────────────────────────
def get_cat_spend(df: pd.DataFrame, cat: str) -> float:
    return df[df["eff_category"] == cat]["net_amount"].abs().sum() if not df.empty else 0.0

def get_prior_cat_spend(cat: str) -> float:
    return get_cat_spend(prior_df[prior_df["net_amount"] < 0], cat) if not prior_df.empty else 0.0

def delta_pct(current: float, prior: float) -> Optional[float]:
    if prior == 0:
        return None
    return round((current - prior) / prior * 100, 1)


total_spend = expense_df["net_amount"].abs().sum()
total_income = income_df["net_amount"].sum()
net_savings = total_income - total_spend

living_cats = ["Food & Dining", "Utilities", "HouseHold", "Accomodation"]
living_spend = sum(get_cat_spend(expense_df, c) for c in living_cats)
food_spend = get_cat_spend(expense_df, "Food & Dining")
shopping_spend = get_cat_spend(expense_df, "Shopping")
travel_spend = get_cat_spend(expense_df, "Travel")
entertainment_spend = get_cat_spend(expense_df, "Entertainment")
investment_spend = get_cat_spend(expense_df, "Investment")

prior_living = sum(get_prior_cat_spend(c) for c in living_cats)
prior_food = get_prior_cat_spend("Food & Dining")
prior_shopping = get_prior_cat_spend("Shopping")
prior_travel = get_prior_cat_spend("Travel")
prior_entertainment = get_prior_cat_spend("Entertainment")
prior_investment = get_prior_cat_spend("Investment")

# ── TOP KPI BAR ───────────────────────────────────────────────────────────────
# Maps KPI label → category name (for drill-through on click)
kpi_label_to_cat = {
    "Food & Dining": "Food & Dining",
    "Shopping": "Shopping",
    "Travel": "Travel",
    "Entertainment": "Entertainment",
    "Investment": "Investment",
}

kpi_cols = st.columns(8)
kpi_data = [
    ("Total Spend",     total_spend,        delta_pct(total_spend, prior_expense),       "normal"),
    ("Living Expenses", living_spend,        delta_pct(living_spend, prior_living),        "normal"),
    ("Food & Dining",   food_spend,          delta_pct(food_spend, prior_food),            "normal"),
    ("Shopping",        shopping_spend,      delta_pct(shopping_spend, prior_shopping),    "normal"),
    ("Travel",          travel_spend,        delta_pct(travel_spend, prior_travel),         "normal"),
    ("Entertainment",   entertainment_spend, delta_pct(entertainment_spend, prior_entertainment), "normal"),
    ("Investment",      investment_spend,    delta_pct(investment_spend, prior_investment), "normal"),
    ("Net Savings",     net_savings,         None,                                          "normal"),
]

active_drill = st.session_state.get("drill", {})

for col, (label, value, delta, _) in zip(kpi_cols, kpi_data):
    delta_str = f"{delta:+.1f}%" if delta is not None else None
    is_active = (
        active_drill.get("type") == "category" and
        active_drill.get("value") == kpi_label_to_cat.get(label)
    )
    border = "2px solid #7cb47c" if is_active else "none"
    col.markdown(
        f'<div style="border:{border}; border-radius:6px; padding:4px;">',
        unsafe_allow_html=True,
    )
    col.metric(
        label,
        f"₹{value:,.0f}",
        delta=delta_str,
        delta_color="inverse" if label != "Net Savings" else "normal",
    )
    cat_name = kpi_label_to_cat.get(label)
    if cat_name:
        if col.button("🔍", key=f"kpi_drill_{label}", help=f"Drill into {label} transactions"):
            _set_drill(type="category", value=cat_name)
            st.rerun()
    col.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# ROW 1: Monthly stacked bar | Monthly net income bar | Account donut
# ══════════════════════════════════════════════════════════════════════════════
row1_cols = st.columns([1, 1, 1])

# ── Col 1: Stacked bar — monthly expenses by top-6 category ──────────────────
with row1_cols[0]:
    st.markdown("#### Monthly Expenses by Category")
    if not expense_df.empty:
        top6_cats = (
            expense_df.groupby("eff_category")["abs_amount"]
            .sum()
            .nlargest(6)
            .index.tolist()
        )
        monthly_cat = (
            expense_df[expense_df["eff_category"].isin(top6_cats)]
            .groupby(["month", "eff_category"])["abs_amount"]
            .sum()
            .reset_index()
        )
        fig_bar = go.Figure()
        for i, cat in enumerate(top6_cats):
            cat_data = monthly_cat[monthly_cat["eff_category"] == cat]
            fig_bar.add_trace(go.Bar(
                x=cat_data["month"],
                y=cat_data["abs_amount"],
                name=cat,
                marker_color=PALETTE[i % len(PALETTE)],
            ))
        fig_bar.update_layout(
            barmode="stack",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="h", y=-0.25, font_size=10),
            margin=dict(l=0, r=0, t=20, b=60),
            xaxis_title="Month",
            yaxis_title="Amount (₹)",
            height=320,
        )
        ev_bar = st.plotly_chart(
            fig_bar, use_container_width=True,
            key="stacked_bar", on_select="rerun",
        )
        if ev_bar and ev_bar.selection and ev_bar.selection.points:
            pt = ev_bar.selection.points[0]
            curve_idx = pt.get("curveNumber", 0)
            clicked_cat = top6_cats[curve_idx] if curve_idx < len(top6_cats) else None
            clicked_month = pt.get("x")
            if clicked_cat:
                _set_drill(type="category", value=clicked_cat, month=clicked_month)
                st.rerun()
    else:
        st.caption("No expense data.")

# ── Col 2: Monthly net income bar ─────────────────────────────────────────────
with row1_cols[1]:
    st.markdown("#### Monthly Net Income")
    monthly_net = (
        df.groupby("month")["net_amount"]
        .sum()
        .reset_index()
        .rename(columns={"net_amount": "net"})
    )
    monthly_net = monthly_net.sort_values("month")
    fig_net = go.Figure(go.Bar(
        x=monthly_net["month"],
        y=monthly_net["net"],
        marker_color=[
            "#7cb47c" if v >= 0 else "#d4889a"
            for v in monthly_net["net"]
        ],
    ))
    fig_net.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=20, b=40),
        xaxis_title="Month",
        yaxis_title="Net (₹)",
        height=320,
    )
    ev_net = st.plotly_chart(
        fig_net, use_container_width=True,
        key="net_bar", on_select="rerun",
    )
    if ev_net and ev_net.selection and ev_net.selection.points:
        pt = ev_net.selection.points[0]
        clicked_month = pt.get("x")
        if clicked_month:
            _set_drill(type="month", value=clicked_month)
            st.rerun()

# ── Col 3: Donut — spend by account ──────────────────────────────────────────
with row1_cols[2]:
    st.markdown("#### Spend by Account")
    acc_spend = (
        expense_df.groupby("account_name")["abs_amount"]
        .sum()
        .reset_index()
    )
    if not acc_spend.empty:
        fig_donut = go.Figure(go.Pie(
            labels=acc_spend["account_name"],
            values=acc_spend["abs_amount"],
            hole=0.55,
            marker=dict(colors=PALETTE),
            textinfo="label+percent",
        ))
        fig_donut.update_layout(
            annotations=[{
                "text": f"₹{total_spend:,.0f}",
                "x": 0.5, "y": 0.5,
                "font_size": 13,
                "showarrow": False,
            }],
            showlegend=False,
            margin=dict(l=0, r=0, t=20, b=0),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            height=320,
        )
        ev_donut = st.plotly_chart(
            fig_donut, use_container_width=True,
            key="donut_chart", on_select="rerun",
        )
        if ev_donut and ev_donut.selection and ev_donut.selection.points:
            pt = ev_donut.selection.points[0]
            clicked_account = pt.get("label")
            if clicked_account:
                _set_drill(type="account", value=clicked_account)
                st.rerun()
    else:
        st.caption("No expense data.")

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# ROW 2: Treemap (60%) | Multi-line trend (40%)
# ══════════════════════════════════════════════════════════════════════════════
row2_cols = st.columns([3, 2])

with row2_cols[0]:
    st.markdown("#### Expense Treemap — YTD")
    if not expense_df.empty:
        treemap_df = (
            expense_df.groupby(["eff_category", "eff_subcategory"])["abs_amount"]
            .sum()
            .reset_index()
        )
        treemap_df = treemap_df[treemap_df["abs_amount"] > 0]
        treemap_df["root"] = "Total"
        fig_tree = px.treemap(
            treemap_df,
            path=["root", "eff_category", "eff_subcategory"],
            values="abs_amount",
            color="eff_category",
            color_discrete_sequence=PALETTE,
        )
        fig_tree.update_layout(
            margin=dict(l=0, r=0, t=20, b=0),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            height=380,
        )
        fig_tree.update_traces(
            texttemplate="<b>%{label}</b><br>₹%{value:,.0f}",
            hovertemplate="<b>%{label}</b><br>₹%{value:,.0f}<extra></extra>",
        )
        ev_tree = st.plotly_chart(
            fig_tree, use_container_width=True,
            key="treemap_chart", on_select="rerun",
        )
        if ev_tree and ev_tree.selection and ev_tree.selection.points:
            pt = ev_tree.selection.points[0]
            label = str(pt.get("label", ""))
            if label and label not in ("Total", ""):
                # Determine if label is a category or subcategory
                if label in expense_df["eff_category"].values:
                    _set_drill(type="category", value=label)
                    st.rerun()
                elif label in expense_df["eff_subcategory"].values:
                    parent_cat = expense_df[
                        expense_df["eff_subcategory"] == label
                    ]["eff_category"].iloc[0]
                    _set_drill(type="subcategory", category=parent_cat, value=label)
                    st.rerun()
    else:
        st.caption("No expense data for treemap.")

with row2_cols[1]:
    st.markdown("#### Monthly Trend — Top 6 Categories")
    if not expense_df.empty:
        top6 = (
            expense_df.groupby("eff_category")["abs_amount"]
            .sum()
            .nlargest(6)
            .index.tolist()
        )
        trend_df = (
            expense_df[expense_df["eff_category"].isin(top6)]
            .groupby(["month", "eff_category"])["abs_amount"]
            .sum()
            .reset_index()
        )
        fig_trend = go.Figure()
        for i, cat in enumerate(top6):
            cat_data = trend_df[trend_df["eff_category"] == cat]
            fig_trend.add_trace(go.Scatter(
                x=cat_data["month"],
                y=cat_data["abs_amount"],
                name=cat,
                mode="lines+markers",
                line=dict(color=PALETTE[i % len(PALETTE)], width=2),
                marker=dict(size=6),
            ))
        fig_trend.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="h", y=-0.35, font_size=9),
            margin=dict(l=0, r=0, t=20, b=80),
            xaxis_title="Month",
            yaxis_title="₹",
            height=380,
        )
        ev_trend = st.plotly_chart(
            fig_trend, use_container_width=True,
            key="trend_chart", on_select="rerun",
        )
        if ev_trend and ev_trend.selection and ev_trend.selection.points:
            pt = ev_trend.selection.points[0]
            curve_idx = pt.get("curveNumber", 0)
            clicked_cat = top6[curve_idx] if curve_idx < len(top6) else None
            clicked_month = pt.get("x")
            if clicked_cat:
                _set_drill(type="category", value=clicked_cat, month=clicked_month)
                st.rerun()
    else:
        st.caption("No trend data.")

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# ROW 3: Top 10 subcategories (horiz bar) | Waterfall chart
# ══════════════════════════════════════════════════════════════════════════════
row3_cols = st.columns(2)

with row3_cols[0]:
    st.markdown("#### Top 10 Subcategories by Spend YTD")
    if not expense_df.empty:
        top10_sub = (
            expense_df.groupby(["eff_category", "eff_subcategory"])["abs_amount"]
            .sum()
            .reset_index()
            .nlargest(10, "abs_amount")
        )
        cat_colors = {
            cat: PALETTE[i % len(PALETTE)]
            for i, cat in enumerate(top10_sub["eff_category"].unique())
        }
        top10_sub["color"] = top10_sub["eff_category"].map(cat_colors)
        top10_sub["y_label"] = (
            top10_sub["eff_subcategory"] + " (" + top10_sub["eff_category"] + ")"
        )
        fig_horiz = go.Figure(go.Bar(
            x=top10_sub["abs_amount"],
            y=top10_sub["y_label"],
            orientation="h",
            marker_color=top10_sub["color"],
            text=top10_sub["abs_amount"].apply(lambda v: f"₹{v:,.0f}"),
            textposition="outside",
        ))
        fig_horiz.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=60, t=20, b=0),
            xaxis_title="Amount (₹)",
            yaxis=dict(autorange="reversed"),
            height=380,
        )
        ev_horiz = st.plotly_chart(
            fig_horiz, use_container_width=True,
            key="horiz_bar", on_select="rerun",
        )
        if ev_horiz and ev_horiz.selection and ev_horiz.selection.points:
            pt = ev_horiz.selection.points[0]
            y_label = str(pt.get("y", ""))
            if "(" in y_label and y_label.endswith(")"):
                sub = y_label[:y_label.rfind("(")].strip()
                cat = y_label[y_label.rfind("(") + 1: -1]
                _set_drill(type="subcategory", category=cat, value=sub)
                st.rerun()
    else:
        st.caption("No subcategory data.")

with row3_cols[1]:
    st.markdown("#### Income & Expense Waterfall")
    if not df.empty:
        salary_total = income_df["net_amount"].sum()
        cat_totals = (
            expense_df.groupby("eff_category")["abs_amount"]
            .sum()
            .nlargest(8)
        )
        labels = ["Income"] + list(cat_totals.index) + ["Net Savings"]
        values = [salary_total] + [-v for v in cat_totals.values] + [0]
        net_shown = salary_total - cat_totals.sum()
        values[-1] = net_shown

        measure = ["absolute"] + ["relative"] * len(cat_totals) + ["total"]
        colors = [
            "#7cb47c" if v >= 0 else "#d4889a"
            for v in [salary_total] + list(-cat_totals.values) + [net_shown]
        ]

        fig_wf = go.Figure(go.Waterfall(
            x=labels,
            y=values,
            measure=measure,
            connector=dict(line=dict(color="#888", width=0.5)),
            increasing=dict(marker_color="#7cb47c"),
            decreasing=dict(marker_color="#d4889a"),
            totals=dict(marker_color="#8b6fba"),
            texttemplate="₹%{y:,.0f}",
            textposition="outside",
        ))
        fig_wf.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=20, b=60),
            xaxis_tickangle=-30,
            yaxis_title="₹",
            height=380,
        )
        ev_wf = st.plotly_chart(
            fig_wf, use_container_width=True,
            key="waterfall_chart", on_select="rerun",
        )
        if ev_wf and ev_wf.selection and ev_wf.selection.points:
            pt = ev_wf.selection.points[0]
            clicked_x = str(pt.get("x", ""))
            if clicked_x and clicked_x not in ("Income", "Net Savings", ""):
                _set_drill(type="category", value=clicked_x)
                st.rerun()
    else:
        st.caption("No data for waterfall.")

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# ROW 4: Heatmap — Month × Category
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("#### Month-over-Month Spend Heatmap")
if not expense_df.empty:
    heatmap_data = (
        expense_df.groupby(["month", "eff_category"])["abs_amount"]
        .sum()
        .reset_index()
    )
    pivot_hm = heatmap_data.pivot_table(
        index="eff_category", columns="month", values="abs_amount", fill_value=0
    )
    pivot_hm = pivot_hm[sorted(pivot_hm.columns)]

    # Limit to top 15 categories by total
    top_cats_hm = pivot_hm.sum(axis=1).nlargest(15).index
    pivot_hm = pivot_hm.loc[top_cats_hm]

    fig_hm = go.Figure(go.Heatmap(
        z=pivot_hm.values,
        x=pivot_hm.columns.tolist(),
        y=pivot_hm.index.tolist(),
        colorscale=[[0, "#1e1e2e"], [0.01, "#ffffff"], [1, "#d4889a"]],
        text=[[f"₹{v:,.0f}" if v > 0 else "" for v in row] for row in pivot_hm.values],
        texttemplate="%{text}",
        textfont=dict(size=9),
        hovertemplate="<b>%{y}</b> | %{x}<br>₹%{z:,.0f}<extra></extra>",
    ))
    fig_hm.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=140, r=0, t=20, b=60),
        xaxis_title="Month",
        xaxis_tickangle=-30,
        height=max(300, len(top_cats_hm) * 28 + 80),
    )
    ev_hm = st.plotly_chart(
        fig_hm, use_container_width=True,
        key="heatmap_chart", on_select="rerun",
    )
    if ev_hm and ev_hm.selection and ev_hm.selection.points:
        pt = ev_hm.selection.points[0]
        clicked_cat = pt.get("y")
        clicked_month = pt.get("x")
        if clicked_cat:
            _set_drill(type="category", value=clicked_cat, month=clicked_month)
            st.rerun()

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# DRILL-THROUGH PANEL
# ══════════════════════════════════════════════════════════════════════════════
drill = st.session_state.get("drill", {})
if drill:
    drill_type = drill.get("type")
    drill_value = drill.get("value")
    drill_month = drill.get("month")
    drill_cat = drill.get("category")  # for subcategory drills

    # Build the filtered DataFrame
    drill_df = df.copy()
    if drill_type == "account":
        drill_df = drill_df[drill_df["account_name"] == drill_value]
        header = f"Account: **{drill_value}**"
    elif drill_type == "category":
        drill_df = drill_df[drill_df["eff_category"] == drill_value]
        header = f"Category: **{drill_value}**"
        if drill_month:
            drill_df = drill_df[drill_df["month"] == drill_month]
            header += f"  ·  Month: **{drill_month}**"
    elif drill_type == "subcategory":
        if drill_cat:
            drill_df = drill_df[
                (drill_df["eff_category"] == drill_cat) &
                (drill_df["eff_subcategory"] == drill_value)
            ]
        else:
            drill_df = drill_df[drill_df["eff_subcategory"] == drill_value]
        header = f"Category: **{drill_cat}** / Subcategory: **{drill_value}**"
    elif drill_type == "month":
        drill_df = drill_df[drill_df["month"] == drill_value]
        header = f"Month: **{drill_value}**"
    else:
        header = "Filtered Transactions"

    hdr_col, btn_col = st.columns([6, 1])
    hdr_col.markdown(f"#### 📋 Drill-through — {header}")
    if btn_col.button("✕ Clear", key="clear_drill_main", use_container_width=True):
        _clear_drill()
        st.rerun()

    if not drill_df.empty:
        d_expense = drill_df[drill_df["net_amount"] < 0]
        d_income  = drill_df[drill_df["net_amount"] > 0]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Transactions", len(drill_df))
        m2.metric("Total Debit",  f"₹{d_expense['abs_amount'].sum():,.2f}")
        m3.metric("Total Credit", f"₹{d_income['net_amount'].sum():,.2f}")
        m4.metric("Net",          f"₹{drill_df['net_amount'].sum():,.2f}")

        show_df = drill_df[["date", "description", "debit", "credit", "net_amount",
                             "account_name", "eff_category", "eff_subcategory"]].copy()
        show_df["date"] = show_df["date"].dt.date
        show_df = show_df.sort_values("date", ascending=False).head(200)

        st.dataframe(
            show_df,
            column_config={
                "date":           st.column_config.DateColumn("Date"),
                "debit":          st.column_config.NumberColumn("Debit",  format="₹%.2f"),
                "credit":         st.column_config.NumberColumn("Credit", format="₹%.2f"),
                "net_amount":     st.column_config.NumberColumn("Net",    format="₹%.2f"),
                "eff_category":   st.column_config.TextColumn("Category"),
                "eff_subcategory":st.column_config.TextColumn("Subcategory"),
            },
            use_container_width=True,
            hide_index=True,
        )
        if len(drill_df) > 200:
            st.caption(f"Showing first 200 of {len(drill_df)} matching transactions.")
    else:
        st.info("No transactions found for this filter.")

    st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# INSIGHTS PANEL
# ══════════════════════════════════════════════════════════════════════════════
with st.expander("💡 Smart Insights", expanded=True):
    ins_cols = st.columns(2)

    with ins_cols[0]:
        # Top 3 overspent vs last month
        st.markdown("##### 📈 Top Overspent vs Prior Period")
        if not expense_df.empty and not prior_df.empty:
            curr_cat = expense_df.groupby("eff_category")["abs_amount"].sum()
            prior_cat = prior_df[prior_df["net_amount"] < 0].groupby("eff_category")["abs_amount"].sum() \
                if not prior_df.empty else pd.Series(dtype=float)

            overspend = []
            for cat in curr_cat.index:
                curr_val = curr_cat.get(cat, 0)
                prior_val = prior_cat.get(cat, 0)
                if prior_val > 0:
                    pct = (curr_val - prior_val) / prior_val * 100
                    overspend.append((cat, curr_val, pct))

            overspend.sort(key=lambda x: -x[2])
            for cat, val, pct in overspend[:3]:
                color = "🔴" if pct > 20 else "🟡"
                st.markdown(f"{color} **{cat}**: ₹{val:,.0f} `+{pct:.1f}%` vs prior period")
        else:
            st.caption("Not enough data for comparison.")

        st.markdown("---")

        # Months where spending exceeded income
        st.markdown("##### ⚠️ Months Over Budget")
        monthly_totals = df.groupby("month")["net_amount"].sum()
        negative_months = monthly_totals[monthly_totals < 0]
        if not negative_months.empty:
            for m, v in negative_months.items():
                st.error(f"🚨 **{m}**: Net ₹{v:,.0f} (spending exceeded income)")
        else:
            st.success("✅ No months where spending exceeded income.")

    with ins_cols[1]:
        # Biggest single transaction
        st.markdown("##### 💸 Biggest Single Transaction")
        if not expense_df.empty:
            biggest = expense_df.nlargest(1, "abs_amount").iloc[0]
            st.metric(
                biggest.get("description", "?")[:50],
                f"₹{biggest['abs_amount']:,.2f}",
                delta=str(biggest.get("date", "?")),
                delta_color="off",
            )

        # Average daily spend
        st.markdown("---")
        st.markdown("##### 📅 Average Daily Spend")
        if not expense_df.empty:
            this_month = datetime.now().strftime("%Y-%m")
            this_month_df = expense_df[expense_df["month"] == this_month]
            curr_day = datetime.now().day
            avg_today = this_month_df["abs_amount"].sum() / max(curr_day, 1)

            all_months = expense_df.groupby("month")["abs_amount"].sum()
            monthly_avg = all_months.mean()
            days_in_month = 30
            avg_daily_all = monthly_avg / days_in_month

            st.metric(
                "This Month (daily avg)",
                f"₹{avg_today:,.0f}",
                delta=f"vs ₹{avg_daily_all:,.0f} historical avg",
                delta_color="off",
            )

        # Outstanding loans
        st.markdown("---")
        st.markdown("##### 🤝 Outstanding Loans")
        loans = get_loan_tags(status="outstanding")
        if loans:
            loan_data = [{
                "Contact": lt["contact_name"],
                "Status": lt["status"],
                "Since": lt.get("created_ts", "?"),
            } for lt in loans]
            st.dataframe(pd.DataFrame(loan_data), use_container_width=True, hide_index=True)
        else:
            st.caption("No outstanding loans.")

    # Budget vs Actual
    st.markdown("---")
    st.markdown("##### 📊 Budget vs Actual")
    budgets = get_budgets()
    if budgets:
        curr_month_str = datetime.now().strftime("%Y-%m")
        curr_month_df = expense_df[expense_df["month"] == curr_month_str] if not expense_df.empty else pd.DataFrame()

        budget_cols = st.columns(min(len(budgets), 4))
        for i, (cat, budget) in enumerate(budgets.items()):
            actual = curr_month_df[curr_month_df["eff_category"] == cat]["abs_amount"].sum() \
                if not curr_month_df.empty else 0.0
            pct = min(actual / budget, 1.0) if budget > 0 else 0.0
            with budget_cols[i % len(budget_cols)]:
                st.markdown(f"**{cat}**")
                st.progress(pct, text=f"₹{actual:,.0f} / ₹{budget:,.0f}")
                if actual > budget:
                    st.caption(f"🔴 Over by ₹{actual - budget:,.0f}")
    else:
        st.caption("No budgets set. Go to Settings → Budget to set monthly category budgets.")
