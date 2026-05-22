"""
pages/4_Dashboard.py
Clean, insight-focused personal finance dashboard.
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

st.set_page_config(
    page_title="Dashboard | Finance Tracker",
    page_icon="📊",
    layout="wide",
)

from core.database import get_transactions, get_loan_tags, get_budgets, init_db
from core.ui_helpers import render_sidebar_stats

init_db()

# ── Colour tokens ───────────────────────────────────────────────────────────────
C_INCOME  = "#10B981"   # emerald
C_EXPENSE = "#EF4444"   # red
C_SAVINGS = "#3B82F6"   # blue
C_INVEST  = "#8B5CF6"   # violet
C_NEUTRAL = "#6B7280"   # grey

CAT_COLORS = [
    "#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6",
    "#EC4899", "#06B6D4", "#84CC16", "#F97316", "#6366F1",
    "#14B8A6", "#F43F5E", "#A78BFA", "#34D399", "#FB923C",
]

# ── Drill-through state ─────────────────────────────────────────────────────────
if "drill" not in st.session_state:
    st.session_state["drill"] = {}


def _set_drill(**kwargs) -> None:
    st.session_state["drill"] = {k: v for k, v in kwargs.items() if v is not None}


def _clear_drill() -> None:
    st.session_state["drill"] = {}


# ── Data helpers ────────────────────────────────────────────────────────────────
def build_df(
    txs: List[Dict],
    start: Optional[date] = None,
    end: Optional[date] = None,
    accounts: Optional[List[str]] = None,
    excl_recon: bool = True,
) -> pd.DataFrame:
    if not txs:
        return pd.DataFrame()
    df = pd.DataFrame(txs)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    if start:
        df = df[df["date"].dt.date >= start]
    if end:
        df = df[df["date"].dt.date <= end]
    if accounts:
        df = df[df["account_name"].isin(accounts)]
    if excl_recon:
        df = df[~df["reconciliation_status"].isin(
            ["transfer_approved", "cc_payment_approved"]
        )]
    df["eff_category"] = (
        df["final_category"].replace("", None)
        .fillna(df["category"].replace("", None))
        .fillna("Uncategorised")
    )
    df["eff_subcategory"] = (
        df["final_subcategory"].replace("", None)
        .fillna(df["subcategory"].replace("", None))
        .fillna("Misc")
    )
    df["month"]     = df["date"].dt.to_period("M").astype(str)
    df["abs_amount"] = df["net_amount"].abs()
    return df


def delta_label(curr: float, prior: float) -> Optional[float]:
    if prior == 0:
        return None
    return round((curr - prior) / abs(prior) * 100, 1)


# ── Load data ───────────────────────────────────────────────────────────────────
render_sidebar_stats()

all_txs = get_transactions()
if not all_txs:
    st.title("📊 Dashboard")
    st.warning("No transactions found. Upload some statements first.")
    st.stop()

all_df_raw = pd.DataFrame(all_txs)
all_df_raw["date"] = pd.to_datetime(all_df_raw["date"], errors="coerce")
all_df_raw = all_df_raw.dropna(subset=["date"])

# ── Sidebar ─────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🔍 Filters")

    min_dt = all_df_raw["date"].min().date()
    max_dt = all_df_raw["date"].max().date()
    default_start = max(date(max_dt.year, 1, 1), min_dt)

    date_range = st.date_input(
        "Date Range",
        value=(default_start, max_dt),
        min_value=min_dt,
        max_value=max_dt,
        key="dash_date",
    )
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = default_start, max_dt

    all_accounts = sorted(all_df_raw["account_name"].dropna().unique())
    sel_accounts = st.multiselect(
        "Accounts", all_accounts, default=all_accounts, key="dash_accs"
    )

    excl_recon = st.toggle(
        "Exclude reconciled transactions", value=True, key="dash_excl"
    )
    excl_invest = st.toggle(
        "Exclude Investments from expenses",
        value=False,
        key="dash_excl_invest",
        help=(
            "When ON, Investment category is removed from expense totals and charts. "
            "Investments are shown separately in the KPI bar."
        ),
    )

    st.markdown("---")
    if st.session_state["drill"]:
        if st.button("✕ Clear drill-through", use_container_width=True):
            _clear_drill()
            st.rerun()

# ── Build dataframes ─────────────────────────────────────────────────────────────
df = build_df(
    all_txs,
    start=start_date,
    end=end_date,
    accounts=sel_accounts or None,
    excl_recon=excl_recon,
)

if df.empty:
    st.title("📊 Dashboard")
    st.warning("No transactions match the current filters.")
    st.stop()

income_df  = df[df["net_amount"] > 0].copy()
expense_df = df[df["net_amount"] < 0].copy()
invest_df  = expense_df[expense_df["eff_category"] == "Investment"].copy()

# View used for charts / KPIs — optionally strips Investment
expense_view = (
    expense_df[expense_df["eff_category"] != "Investment"].copy()
    if excl_invest
    else expense_df.copy()
)

# ── Prior period ─────────────────────────────────────────────────────────────────
period_days = max((end_date - start_date).days, 1)
prior_end   = start_date - timedelta(days=1)
prior_start = prior_end  - timedelta(days=period_days - 1)
prior_df    = build_df(all_txs, start=prior_start, end=prior_end, excl_recon=excl_recon)
prior_expense_df = prior_df[prior_df["net_amount"] < 0].copy() if not prior_df.empty else pd.DataFrame()
if excl_invest and not prior_expense_df.empty:
    prior_expense_df = prior_expense_df[prior_expense_df["eff_category"] != "Investment"]

# ── KPI values ───────────────────────────────────────────────────────────────────
total_income  = income_df["net_amount"].sum()
total_expense = expense_view["net_amount"].abs().sum()
invest_total  = invest_df["net_amount"].abs().sum()
net_savings   = total_income - expense_df["net_amount"].abs().sum()   # always subtract all exp
savings_rate  = (net_savings / total_income * 100) if total_income > 0 else 0.0

prior_income   = prior_df[prior_df["net_amount"] > 0]["net_amount"].sum() if not prior_df.empty else 0
prior_exp_val  = prior_expense_df["net_amount"].abs().sum() if not prior_expense_df.empty else 0
prior_invest   = prior_expense_df[prior_expense_df["eff_category"] == "Investment"]["net_amount"].abs().sum() \
    if (not prior_expense_df.empty and not excl_invest) else 0
prior_savings  = prior_income - prior_df[prior_df["net_amount"] < 0]["net_amount"].abs().sum() \
    if not prior_df.empty else 0
prior_srate    = (prior_savings / prior_income * 100) if prior_income > 0 else 0

# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("## 📊 Personal Finance Dashboard")
col_head, col_tag = st.columns([5, 1])
col_head.markdown(
    f"**{start_date.strftime('%d %b %Y')}** → **{end_date.strftime('%d %b %Y')}**"
)
if excl_invest:
    col_tag.markdown(
        '<span style="background:#8B5CF6;color:white;padding:3px 10px;'
        'border-radius:12px;font-size:12px;">Invest excluded</span>',
        unsafe_allow_html=True,
    )

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# KPI ROW
# ══════════════════════════════════════════════════════════════════════════════
k1, k2, k3, k4, k5 = st.columns(5)

k1.metric(
    "💰 Total Income",
    f"₹{total_income:,.0f}",
    delta=f"{delta_label(total_income, prior_income):+.1f}%" if delta_label(total_income, prior_income) else None,
)
k2.metric(
    "💸 Expenses" + (" (ex-Invest)" if excl_invest else ""),
    f"₹{total_expense:,.0f}",
    delta=f"{delta_label(total_expense, prior_exp_val):+.1f}%" if delta_label(total_expense, prior_exp_val) else None,
    delta_color="inverse",
)
if excl_invest:
    k3.metric(
        "📈 Invested",
        f"₹{invest_total:,.0f}",
        delta=f"{delta_label(invest_total, prior_invest):+.1f}%" if delta_label(invest_total, prior_invest) else None,
        delta_color="normal",
    )
else:
    k3.metric("📈 Invested", f"₹{invest_total:,.0f}")

k4.metric(
    "🏦 Net Savings",
    f"₹{net_savings:,.0f}",
    delta=f"{delta_label(net_savings, prior_savings):+.1f}%" if delta_label(net_savings, prior_savings) else None,
    delta_color="normal",
)

sr_delta = round(savings_rate - prior_srate, 1) if prior_srate != 0 else None
k5.metric(
    "📉 Savings Rate",
    f"{savings_rate:.1f}%",
    delta=f"{sr_delta:+.1f}pp" if sr_delta is not None else None,
    delta_color="normal" if (sr_delta or 0) >= 0 else "inverse",
)

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# ROW 1 — Monthly Cash Flow (wide) | Expense Breakdown (donut)
# ══════════════════════════════════════════════════════════════════════════════
r1a, r1b = st.columns([3, 2])

with r1a:
    st.markdown("#### 📅 Monthly Cash Flow")
    all_months = sorted(df["month"].unique())

    m_inc  = income_df.groupby("month")["net_amount"].sum().reindex(all_months, fill_value=0)
    m_exp  = expense_view.groupby("month")["net_amount"].apply(
        lambda x: x.abs().sum()
    ).reindex(all_months, fill_value=0)
    m_inv  = invest_df.groupby("month")["abs_amount"].sum().reindex(all_months, fill_value=0)
    m_net  = m_inc - expense_df.groupby("month")["net_amount"].apply(
        lambda x: x.abs().sum()
    ).reindex(all_months, fill_value=0)

    fig_cf = go.Figure()
    fig_cf.add_trace(go.Bar(
        x=all_months, y=m_inc.values,
        name="Income", marker_color=C_INCOME, opacity=0.85,
        hovertemplate="Income: ₹%{y:,.0f}<extra></extra>",
    ))
    fig_cf.add_trace(go.Bar(
        x=all_months, y=m_exp.values,
        name="Expenses" + (" (ex-Invest)" if excl_invest else ""),
        marker_color=C_EXPENSE, opacity=0.85,
        hovertemplate="Expenses: ₹%{y:,.0f}<extra></extra>",
    ))
    if excl_invest and m_inv.sum() > 0:
        fig_cf.add_trace(go.Bar(
            x=all_months, y=m_inv.values,
            name="Investment", marker_color=C_INVEST, opacity=0.85,
            hovertemplate="Investment: ₹%{y:,.0f}<extra></extra>",
        ))
    fig_cf.add_trace(go.Scatter(
        x=all_months, y=m_net.values,
        name="Net Savings",
        mode="lines+markers",
        line=dict(color=C_SAVINGS, width=2.5, dash="dot"),
        marker=dict(size=7, color=C_SAVINGS),
        hovertemplate="Net: ₹%{y:,.0f}<extra></extra>",
    ))
    fig_cf.update_layout(
        barmode="group",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", y=-0.25, font_size=11, bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=0, r=0, t=10, b=60),
        height=330,
        xaxis=dict(showgrid=False, title=None),
        yaxis=dict(gridcolor="#F3F4F6", tickprefix="₹", title=None),
        hovermode="x unified",
    )
    ev_cf = st.plotly_chart(fig_cf, use_container_width=True, key="cf_chart", on_select="rerun")
    if ev_cf and ev_cf.selection and ev_cf.selection.points:
        pt = ev_cf.selection.points[0]
        clicked_month = pt.get("x")
        if clicked_month:
            _set_drill(type="month", value=clicked_month)
            st.rerun()

with r1b:
    st.markdown("#### 🧩 Expense Breakdown")
    if not expense_view.empty:
        cat_totals = (
            expense_view.groupby("eff_category")["abs_amount"]
            .sum()
            .sort_values(ascending=False)
        )
        # Group beyond top 8 as "Other"
        if len(cat_totals) > 8:
            top8  = cat_totals.iloc[:8]
            other = cat_totals.iloc[8:].sum()
            cat_totals = pd.concat([top8, pd.Series({"Other": other})])

        fig_donut = go.Figure(go.Pie(
            labels=cat_totals.index,
            values=cat_totals.values,
            hole=0.62,
            marker=dict(colors=CAT_COLORS[:len(cat_totals)], line=dict(width=1.5, color="white")),
            textinfo="label+percent",
            textfont_size=10,
            hovertemplate="<b>%{label}</b><br>₹%{value:,.0f} (%{percent})<extra></extra>",
        ))
        fig_donut.update_layout(
            annotations=[{
                "text": f"<b>₹{total_expense:,.0f}</b>",
                "x": 0.5, "y": 0.5,
                "font": dict(size=13, color="#1F2937"),
                "showarrow": False,
            }],
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=0),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            height=330,
        )
        ev_donut = st.plotly_chart(
            fig_donut, use_container_width=True, key="donut_chart", on_select="rerun"
        )
        if ev_donut and ev_donut.selection and ev_donut.selection.points:
            pt = ev_donut.selection.points[0]
            label = pt.get("label")
            if label and label != "Other":
                _set_drill(type="category", value=label)
                st.rerun()
    else:
        st.caption("No expense data.")

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# ROW 2 — Top Categories (bar) | Treemap
# ══════════════════════════════════════════════════════════════════════════════
r2a, r2b = st.columns([2, 3])

with r2a:
    st.markdown("#### 🏷️ Top Spending Categories")
    if not expense_view.empty:
        top_cats = (
            expense_view.groupby("eff_category")["abs_amount"]
            .sum()
            .sort_values(ascending=True)
            .tail(10)
        )
        total_for_pct = top_cats.sum()
        pct = (top_cats / total_for_pct * 100).round(1) if total_for_pct > 0 else top_cats * 0

        colors = [CAT_COLORS[i % len(CAT_COLORS)] for i in range(len(top_cats))]

        fig_cats = go.Figure(go.Bar(
            x=top_cats.values,
            y=top_cats.index,
            orientation="h",
            marker=dict(color=colors, line=dict(width=0)),
            text=[f"₹{v:,.0f}  {p:.1f}%" for v, p in zip(top_cats.values, pct.values)],
            textposition="outside",
            textfont=dict(size=10),
            hovertemplate="<b>%{y}</b><br>₹%{x:,.0f}<extra></extra>",
        ))
        fig_cats.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=90, t=10, b=0),
            xaxis=dict(visible=False),
            yaxis=dict(showgrid=False, tickfont=dict(size=11)),
            height=380,
        )
        ev_cats = st.plotly_chart(
            fig_cats, use_container_width=True, key="cats_bar", on_select="rerun"
        )
        if ev_cats and ev_cats.selection and ev_cats.selection.points:
            pt = ev_cats.selection.points[0]
            cat = pt.get("y")
            if cat:
                _set_drill(type="category", value=cat)
                st.rerun()
    else:
        st.caption("No data.")

with r2b:
    st.markdown("#### 🗺️ Expense Treemap")
    if not expense_view.empty:
        tm_df = (
            expense_view.groupby(["eff_category", "eff_subcategory"])["abs_amount"]
            .sum()
            .reset_index()
        )
        tm_df = tm_df[tm_df["abs_amount"] > 0]
        tm_df["root"] = "All Expenses"

        unique_cats = tm_df["eff_category"].unique().tolist()
        cat_cmap = {c: CAT_COLORS[i % len(CAT_COLORS)] for i, c in enumerate(unique_cats)}

        fig_tree = px.treemap(
            tm_df,
            path=["root", "eff_category", "eff_subcategory"],
            values="abs_amount",
            color="eff_category",
            color_discrete_map={"(?)": "#9CA3AF", **cat_cmap},
        )
        fig_tree.update_layout(
            margin=dict(l=0, r=0, t=10, b=0),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            height=380,
        )
        fig_tree.update_traces(
            texttemplate="<b>%{label}</b><br>₹%{value:,.0f}",
            hovertemplate="<b>%{label}</b><br>₹%{value:,.0f}<extra></extra>",
        )
        ev_tree = st.plotly_chart(
            fig_tree, use_container_width=True, key="treemap_chart", on_select="rerun"
        )
        if ev_tree and ev_tree.selection and ev_tree.selection.points:
            pt = ev_tree.selection.points[0]
            label = str(pt.get("label", ""))
            if label and label not in ("All Expenses", ""):
                if label in expense_view["eff_category"].values:
                    _set_drill(type="category", value=label)
                    st.rerun()
                elif label in expense_view["eff_subcategory"].values:
                    parent = expense_view[
                        expense_view["eff_subcategory"] == label
                    ]["eff_category"].iloc[0]
                    _set_drill(type="subcategory", category=parent, value=label)
                    st.rerun()
    else:
        st.caption("No data.")

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# ROW 3 — Monthly Savings Rate | Budget vs Actual
# ══════════════════════════════════════════════════════════════════════════════
r3a, r3b = st.columns([2, 3])

with r3a:
    st.markdown("#### 💹 Monthly Savings Rate")
    m_inc2  = income_df.groupby("month")["net_amount"].sum().reindex(all_months, fill_value=0)
    m_allexp = expense_df.groupby("month")["net_amount"].apply(
        lambda x: x.abs().sum()
    ).reindex(all_months, fill_value=0)
    m_net2  = m_inc2 - m_allexp
    safe_inc = m_inc2.replace(0, np.nan)
    m_srate = (m_net2 / safe_inc * 100).fillna(0)

    fig_sr = go.Figure()
    fig_sr.add_trace(go.Bar(
        x=all_months, y=m_srate.values,
        marker_color=[C_SAVINGS if v >= 0 else C_EXPENSE for v in m_srate.values],
        text=[f"{v:.1f}%" for v in m_srate.values],
        textposition="outside",
        textfont=dict(size=10),
        hovertemplate="Savings Rate: %{y:.1f}%<extra></extra>",
    ))
    fig_sr.add_hline(
        y=0, line_color="#9CA3AF", line_width=1,
    )
    fig_sr.add_hline(
        y=20, line_dash="dash", line_color=C_SAVINGS, line_width=1.2,
        annotation_text="20% target",
        annotation_position="top right",
        annotation_font=dict(size=9, color=C_SAVINGS),
    )
    fig_sr.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=30, b=40),
        yaxis=dict(title="Savings Rate %", gridcolor="#F3F4F6"),
        xaxis=dict(showgrid=False, title=None),
        showlegend=False,
        height=350,
    )
    st.plotly_chart(fig_sr, use_container_width=True, key="srate_chart")

with r3b:
    st.markdown("#### 📊 Budget vs Actual (Current Month)")
    budgets = get_budgets()
    curr_month_str = datetime.now().strftime("%Y-%m")
    curr_month_exp = (
        expense_view[expense_view["month"] == curr_month_str]
        if not expense_view.empty
        else pd.DataFrame()
    )

    if budgets:
        rows = []
        for cat, budget in budgets.items():
            actual = (
                curr_month_exp[curr_month_exp["eff_category"] == cat]["abs_amount"].sum()
                if not curr_month_exp.empty
                else 0.0
            )
            rows.append({
                "cat": cat,
                "budget": budget,
                "actual": actual,
                "used": min(actual, budget),
                "remaining": max(budget - actual, 0),
                "over": max(actual - budget, 0),
            })

        bdf = pd.DataFrame(rows).sort_values("actual", ascending=True)
        bar_colors = [C_EXPENSE if r > 0 else C_SAVINGS for r in bdf["over"]]

        fig_bud = go.Figure()
        fig_bud.add_trace(go.Bar(
            y=bdf["cat"],
            x=bdf["used"],
            orientation="h",
            name="Spent",
            marker=dict(color=bar_colors),
            text=[f"₹{v:,.0f}" for v in bdf["actual"]],
            textposition="inside",
            textfont=dict(size=10, color="white"),
            hovertemplate="<b>%{y}</b><br>Spent: ₹%{x:,.0f}<extra></extra>",
        ))
        fig_bud.add_trace(go.Bar(
            y=bdf["cat"],
            x=bdf["remaining"],
            orientation="h",
            name="Remaining",
            marker_color="#E5E7EB",
            hovertemplate="<b>%{y}</b><br>Remaining: ₹%{x:,.0f}<extra></extra>",
        ))
        # Add budget line markers
        for _, row in bdf.iterrows():
            fig_bud.add_shape(
                type="line",
                x0=row["budget"], x1=row["budget"],
                y0=str(row["cat"]),
                y1=str(row["cat"]),
                yref="y",
                line=dict(color="#6B7280", width=2, dash="dot"),
            )
        fig_bud.update_layout(
            barmode="stack",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=10, b=40),
            xaxis=dict(title="₹", gridcolor="#F3F4F6"),
            yaxis=dict(showgrid=False, tickfont=dict(size=11)),
            legend=dict(orientation="h", y=-0.2, bgcolor="rgba(0,0,0,0)"),
            height=350,
        )
        st.plotly_chart(fig_bud, use_container_width=True, key="budget_chart")

        over = bdf[bdf["over"] > 0]
        for _, r in over.iterrows():
            st.warning(f"⚠️ **{r['cat']}** over budget by ₹{r['over']:,.0f}")
    else:
        st.info("No budgets set. Go to **Settings → Budget** to add monthly limits.")

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# INSIGHTS ROW
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("#### 💡 Key Insights")
ins1, ins2, ins3, ins4 = st.columns(4)

with ins1:
    with st.container(border=True):
        st.markdown("**🔝 Top Category**")
        if not expense_view.empty:
            by_cat = expense_view.groupby("eff_category")["abs_amount"].sum()
            top_cat = by_cat.idxmax()
            top_val = by_cat.max()
            pct = top_val / total_expense * 100 if total_expense > 0 else 0
            st.metric(top_cat, f"₹{top_val:,.0f}", delta=f"{pct:.1f}% of expenses", delta_color="off")
        else:
            st.caption("No data")

with ins2:
    with st.container(border=True):
        st.markdown("**📅 Avg Daily Spend**")
        if not expense_view.empty:
            n_days = max((end_date - start_date).days, 1)
            avg_period = total_expense / n_days
            cm_df = expense_view[expense_view["month"] == curr_month_str]
            cm_daily = cm_df["abs_amount"].sum() / max(datetime.now().day, 1)
            st.metric(
                "This month",
                f"₹{cm_daily:,.0f} /day",
                delta=f"Period avg ₹{avg_period:,.0f}",
                delta_color="off",
            )
        else:
            st.caption("No data")

with ins3:
    with st.container(border=True):
        st.markdown("**💸 Biggest Transaction**")
        if not expense_view.empty:
            big = expense_view.nlargest(1, "abs_amount").iloc[0]
            st.metric(
                str(big.get("date", ""))[:10],
                f"₹{big['abs_amount']:,.0f}",
                delta=str(big.get("description", ""))[:35],
                delta_color="off",
            )
        else:
            st.caption("No data")

with ins4:
    with st.container(border=True):
        st.markdown("**📈 vs Prior Period**")
        if not expense_view.empty and not prior_expense_df.empty:
            curr_by = expense_view.groupby("eff_category")["abs_amount"].sum()
            prev_by = prior_expense_df.groupby("eff_category")["abs_amount"].sum()
            changes = [
                (cat, curr_by[cat], (curr_by[cat] - prev_by.get(cat, 0)) / prev_by[cat] * 100)
                for cat in curr_by.index
                if prev_by.get(cat, 0) > 0
            ]
            if changes:
                changes.sort(key=lambda x: -x[2])
                cat, val, pct = changes[0]
                icon = "🔴" if pct > 20 else "🟡"
                st.metric(
                    f"{icon} {cat}",
                    f"₹{val:,.0f}",
                    delta=f"{pct:+.1f}% vs prior",
                    delta_color="inverse",
                )
            else:
                st.caption("No prior period data.")
        else:
            st.caption("Need prior period data.")

# Months where spending exceeded income
if not df.empty:
    monthly_net = df.groupby("month")["net_amount"].sum()
    neg_months = monthly_net[monthly_net < 0]
    if not neg_months.empty:
        st.markdown("")
        for m, v in neg_months.items():
            st.error(
                f"🚨 **{m}**: Spending exceeded income by ₹{abs(v):,.0f}"
            )

# Outstanding loans
loans = get_loan_tags(status="outstanding")
if loans:
    st.markdown("")
    with st.container(border=True):
        st.markdown("**🤝 Outstanding Loans**")
        loan_data = [
            {"Contact": lt["contact_name"], "Status": lt["status"]}
            for lt in loans
        ]
        st.dataframe(pd.DataFrame(loan_data), use_container_width=True, hide_index=True)

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# DRILL-THROUGH PANEL
# ══════════════════════════════════════════════════════════════════════════════
drill = st.session_state.get("drill", {})
if drill:
    drill_type  = drill.get("type")
    drill_value = drill.get("value")
    drill_month = drill.get("month")
    drill_cat   = drill.get("category")

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
        header = f"**{drill_cat}** / **{drill_value}**"
    elif drill_type == "month":
        drill_df = drill_df[drill_df["month"] == drill_value]
        header = f"Month: **{drill_value}**"
    else:
        header = "Transactions"

    hc, bc = st.columns([6, 1])
    hc.markdown(f"#### 📋 Transactions — {header}")
    if bc.button("✕ Clear", key="clear_drill", use_container_width=True):
        _clear_drill()
        st.rerun()

    if not drill_df.empty:
        d_exp = drill_df[drill_df["net_amount"] < 0]
        d_inc = drill_df[drill_df["net_amount"] > 0]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Transactions", len(drill_df))
        m2.metric("Total Debit",  f"₹{d_exp['abs_amount'].sum():,.2f}")
        m3.metric("Total Credit", f"₹{d_inc['net_amount'].sum():,.2f}")
        m4.metric("Net",          f"₹{drill_df['net_amount'].sum():,.2f}")

        show = drill_df[[
            "date", "description", "debit", "credit", "net_amount",
            "account_name", "eff_category", "eff_subcategory",
        ]].copy()
        show["date"] = show["date"].dt.date
        show = show.sort_values("date", ascending=False).head(200)

        st.dataframe(
            show,
            column_config={
                "date":            st.column_config.DateColumn("Date"),
                "debit":           st.column_config.NumberColumn("Debit",        format="₹%.2f"),
                "credit":          st.column_config.NumberColumn("Credit",       format="₹%.2f"),
                "net_amount":      st.column_config.NumberColumn("Net",          format="₹%.2f"),
                "eff_category":    st.column_config.TextColumn("Category"),
                "eff_subcategory": st.column_config.TextColumn("Subcategory"),
            },
            use_container_width=True,
            hide_index=True,
        )
        if len(drill_df) > 200:
            st.caption(f"Showing first 200 of {len(drill_df)} transactions.")
    else:
        st.info("No transactions found for this filter.")
