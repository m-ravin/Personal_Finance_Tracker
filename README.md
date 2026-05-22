# Personal Finance Tracker

A production-quality multi-page Streamlit application for tracking personal finances — upload bank and credit card statements, reconcile transfers and CC payments, auto-categorise transactions, and explore your spending through an insight-focused interactive dashboard.

---

## Features

- **Multi-format statement ingestion** — CSV, XLSX, and PDF with flexible column mapping and saved profiles per account
- **Smart column detection** — auto-detects date, description, debit/credit, amount, and sign columns; supports Indian bank formats (HDFC, ICICI, SBI, Axis, etc.)
- **Credit card support** — handles single-amount + sign column (e.g. `BillingAmountSign`) formats; explicit `CR`/`DR` in sign column overrides; otherwise positive amount → credit, negative → debit
- **Reconciliation engine** — three engines: internal transfers (bank ↔ bank), CC bill payments (bank debit ↔ CC credit), and personal loans (fuzzy contact matching)
- **Smart categorisation** — three-tier pipeline: exact keyword match → fuzzy match (rapidfuzz ≥ 75) → optional LLM (Claude / OpenAI / Groq)
- **Interactive dashboard** — KPIs, cash flow, treemap, savings rate, budget vs actual, insights cards, and click-through drill-down
- **Investment exclusion** — toggle to remove Investment category from expense totals and charts; tracked separately in the KPI bar
- **Budget tracking** — set monthly budgets per category, progress bars, over-budget alerts
- **Data management** — soft-delete, permanent purge, SQLite backup/restore, column mapping profiles

---

## Quick Start

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone the repo
git clone <repo-url>
cd Personal_Finance_Tracker

# Install dependencies and run
uv sync
uv run streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## Project Structure

```
Personal_Finance_Tracker/
├── app.py                        # Entry point & navigation shell
├── pyproject.toml                # uv-managed dependencies (Python ≥ 3.11)
├── .python-version               # Pinned to 3.11
├── .env                          # API keys (gitignored)
├── .streamlit/
│   └── config.toml               # Dark theme configuration
├── pages/
│   ├── 1_Upload.py               # Statement upload, column mapping, auto-import
│   ├── 2_Reconcile.py            # Transfer / CC payment / loan reconciliation
│   ├── 3_Categorise.py           # Manual category overrides + LLM re-categorise
│   ├── 4_Dashboard.py            # KPIs, charts, insights, drill-through
│   └── 5_Settings.py             # LLM config, budgets, categories, data management
├── core/
│   ├── database.py               # SQLAlchemy schema & all CRUD operations
│   ├── ingestion.py              # CSV/XLSX/PDF parsers + column auto-mapping
│   ├── reconciliation.py         # Transfer, CC payment & loan matching engines
│   ├── categorisation.py         # Keyword → fuzzy → LLM pipeline
│   ├── llm.py                    # Multi-provider LLM abstraction
│   └── ui_helpers.py             # Shared sidebar stats + export buttons
├── data/
│   ├── categories.json           # 21 pre-seeded categories with keywords
│   └── finance.db                # SQLite database (auto-created, gitignored)
└── README.md
```

---

## Pages

### 1. Upload
Upload one or more statement files. The app auto-detects column mappings and saves them per account name so future uploads need no manual configuration. Shows a preview of imported transactions with totals and triggers automatic reconciliation scanning after each import.

### 2. Reconcile
Three reconciliation workflows:
- **Internal Transfers** — matches a debit in one account to a credit in another within ±3 days and 0.5% amount tolerance
- **CC Payments** — matches a bank debit to a credit card credit (bill payment) within ±5 days
- **Personal Loans** — tag outgoing transactions as loans given; fuzzy-match incoming credits to find repayments

Approved pairs are excluded from Dashboard expense totals automatically.

### 3. Categorise
Browse all transactions, correct categories, and trigger LLM re-categorisation. Category assignments are soft-overridden (`final_category`) without touching the auto-detected values.

### Categories (`data/categories.json`)

Categories and their keywords are defined in `data/categories.json`. The file ships with 21 pre-seeded categories (Food & Dining, Travel, Shopping, Utilities, Investment, etc.), each with subcategories and keyword lists used by the auto-categorisation engine.

**To customise categories**, edit `data/categories.json` directly:

```jsonc
{
  "categories": [
    {
      "category": "Food & Dining",
      "subcategories": [
        {
          "subcategory": "Delivery",
          "type_tag": "Delivery",
          "keywords": ["Swiggy", "Zomato", "YOUR_APP"]   // ← add your keywords here
        }
      ]
    }
  ]
}
```

You can also manage keywords through **Settings → Categories** in the UI without editing the file directly.

**Alternatively, enable the LLM** (Settings → LLM Configuration) to have Claude, GPT-4o, or Llama automatically categorise transactions that don't match any keyword — useful when your statements contain merchant names not yet in the keyword list.

### 4. Dashboard
See [Dashboard Overview](#dashboard-overview) below.

### 5. Settings
- **LLM** — pick provider (None / Claude / OpenAI / Groq), enter API key, test connection
- **Budgets** — set monthly spending limits per category
- **Categories** — view and edit the keyword list for each category
- **Data Management** — soft-delete by account or date range, purge permanently, backup/restore the SQLite database
- **Column Mapping Profiles** — view and delete saved mapping profiles

---

## Dashboard Overview

The dashboard provides a full-page finance view with sidebar filters and six chart sections.

### Sidebar Controls

```
┌─────────────────────────┐
│  🔍 Filters             │
│  ┌───────────────────┐  │
│  │ Date Range        │  │
│  │ 01 Jan – 31 Dec   │  │
│  └───────────────────┘  │
│  Accounts  [All ▼]      │
│  ☑ Exclude reconciled   │
│  ☐ Exclude Investments  │
└─────────────────────────┘
```

- **Date Range** — defaults to the current calendar year; any range is selectable
- **Accounts** — multi-select to compare or isolate individual accounts
- **Exclude reconciled** — removes transfer-approved and CC-payment-approved transactions (on by default)
- **Exclude Investments** — strips the Investment category from expense charts and KPIs; tracked separately

---

### Layout Snapshot

```
┌──────────────────────────────────────────────────────────────┐
│  📊 Personal Finance Dashboard   01 Jan – 31 Dec 2025        │
│                                         [Invest excluded]     │
├──────────────┬──────────────┬──────────┬──────────┬──────────┤
│ 💰 Income    │ 💸 Expenses  │ 📈 Invest│ 🏦 Savings│ 📉 Rate │
│ ₹3,20,000   │ ₹1,85,000   │ ₹40,000 │ ₹95,000  │ 29.7%   │
│ +12.4% ↑    │ +3.1% ↑     │ +8.0% ↑ │ +6.2% ↑  │ +2.1pp  │
├──────────────────────────────────┬───────────────────────────┤
│  📅 Monthly Cash Flow            │  🧩 Expense Breakdown     │
│                                  │                           │
│  ██ Income  ██ Expenses  ·· Net  │      Food 22%  ╮          │
│                                  │   Travel 18%  ╯ (donut)  │
│  Jan Feb Mar Apr May Jun Jul …   │  Shopping 15%             │
│                                  │  Utilities 12%  …         │
├──────────────────┬───────────────┴───────────────────────────┤
│  🏷️ Top Spending │  🗺️ Expense Treemap                       │
│                  │                                           │
│  Food      ████ │  ┌─────────────┬──────────┬──────┐        │
│  Travel   ████  │  │    Food     │  Travel  │ Shop │        │
│  Shopping ███   │  │  Groceries  │  Flights │      │        │
│  Utilities ██   │  ├─────────────┴──────────┤      │        │
│  …              │  │     Utilities           │      │        │
│                  │  └────────────────────────┴──────┘        │
├──────────────────┬───────────────────────────────────────────┤
│  💹 Savings Rate │  📊 Budget vs Actual (Current Month)      │
│                  │                                           │
│  30%  ──── 20%  │  Food       ████████░░░░  ₹8,200          │
│  target line     │  Shopping   ████████████! ₹12,500 ⚠️     │
│  ██ ██ ██ ██ ██ │  Utilities  ██████░░░░░░  ₹4,800          │
│  J  F  M  A  M  │                                           │
├──────┬──────────┬──────────────────────┬─────────────────────┤
│  🔝  │  📅      │  💸                  │  📈                 │
│  Top │  Daily   │  Biggest             │  vs Prior Period    │
│  Cat │  Spend   │  Transaction         │                     │
│ Food │ ₹625/day│ ₹18,500 (12 Mar)    │ 🔴 Travel +34.2%    │
└──────┴──────────┴──────────────────────┴─────────────────────┘
```

---

### Chart Details

| Section | Chart | Description |
|---------|-------|-------------|
| **Row 1** | Monthly Cash Flow | Grouped bar chart — Income (green), Expenses (red), Investment (violet when excluded) bars + dotted Net Savings line. Click a month to drill through. |
| **Row 1** | Expense Breakdown | Donut chart — top 8 categories + "Other". Total shown in centre. Click a slice to drill through. |
| **Row 2** | Top Spending Categories | Horizontal bar chart — top 10 categories, sorted by spend, with ₹ and % labels. Click a bar to drill through. |
| **Row 2** | Expense Treemap | Two-level treemap — Category → Subcategory, coloured per category. Click to drill into subcategory. |
| **Row 3** | Monthly Savings Rate | Bar chart per month — blue if positive, red if negative. Dashed 20% target reference line. |
| **Row 3** | Budget vs Actual | Stacked horizontal bar per budgeted category — spent (green/red) + remaining (grey). Over-budget rows trigger ⚠️ alerts below the chart. |

---

### Insights Cards

Four summary cards rendered below the charts:

- **Top Category** — highest-spend category with % of total expenses
- **Avg Daily Spend** — current month daily rate vs period average
- **Biggest Transaction** — largest single expense with date and description
- **vs Prior Period** — the category with the biggest percentage increase vs the equivalent prior period

---

### Drill-Through

Click any chart bar, slice, or treemap cell to open a transaction table filtered to that dimension. The panel shows totals (count, debit, credit, net) and up to 200 transactions. A "✕ Clear drill-through" button in the sidebar resets the filter.

---

## Configuration

### LLM (Optional)

Go to **Settings → LLM Configuration**:

| Provider | Model |
|----------|-------|
| None (default) | keyword + fuzzy only |
| Anthropic Claude | `claude-sonnet-4-20250514` |
| OpenAI | `gpt-4o-mini` |
| Groq | `llama3-70b-8192` (free tier) |

API keys are stored in session state and optionally persisted to `.env`. They are never written to the database.

### Environment Variables

Create a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
```

---

## Technology Stack

| Layer | Library | Version |
|-------|---------|---------|
| UI | Streamlit | ≥ 1.55 |
| Charts | Plotly | ≥ 6.6 |
| Database | SQLite via SQLAlchemy | ≥ 2.0 |
| Data | pandas + numpy | ≥ 2.x |
| Fuzzy matching | rapidfuzz + thefuzz | ≥ 3.14 |
| PDF parsing | pdfplumber | ≥ 0.11 |
| Excel | openpyxl | ≥ 3.1 |
| LLM | anthropic / openai / groq | latest |
| Env | python-dotenv | ≥ 1.2 |
| Package manager | uv | — |

---

## Design Decisions

1. **Permanent SQLite storage** — the database persists across restarts; upload once and return later.
2. **Soft-deletes** — all deletes are initially soft (`is_deleted=1`); a separate purge step removes data permanently.
3. **Reconciliation excludes from spend** — approved transfer and CC payment pairs are stripped from all Dashboard calculations automatically.
4. **Column mapping profiles** — saved per `account_name`; repeat uploads require zero re-mapping. Stale profiles are auto-corrected when new columns (e.g. sign columns) are detected.
5. **CC sign-column defence** — three-layer detection: auto-map → CC heuristic scan → fallback. For credit card accounts with a single amount column, blank sign = debit (purchase), CR = credit (payment/refund).
6. **Investment exclusion** — a sidebar toggle separates investment spend from day-to-day expenses without deleting or re-categorising any data.
7. **LLM is entirely optional** — if disabled, categorisation falls back to keyword → fuzzy → Uncategorised.
8. **Prior period comparison** — KPI deltas and the "vs Prior Period" insight card automatically compute the equivalent preceding date range.

---

## Development

```bash
uv sync                          # install / update dependencies
uv run streamlit run app.py      # start dev server (http://localhost:8501)
uv add <package>                 # add a new dependency
```
