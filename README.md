# Personal Finance Tracker

A production-quality multi-page Streamlit application for tracking personal finances — uploading bank/credit card statements, reconciling transfers, categorising transactions, and visualising spending via an interactive dashboard.

## Features

- **Multi-bank statement ingestion** — CSV, XLSX, and PDF with flexible column mapping
- **Reconciliation engine** — auto-detect internal transfers, credit card payments, and personal loans
- **Smart categorisation** — keyword matching → fuzzy matching → optional LLM (Claude / OpenAI / Groq)
- **Interactive dashboard** — 10+ Plotly charts with KPI bar, treemap, waterfall, and heatmap
- **Budget tracking** — set monthly budgets per category, see progress bars
- **Data management** — soft-delete, permanent purge, backup/restore SQLite

## Quick Start

```bash
# Install uv if not present
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and run
git clone <repo>
cd personal-finance
uv sync
uv run streamlit run app.py
```

## Project Structure

```
personal_finance/
├── app.py                        # Entry point, navigation
├── pyproject.toml                # uv-managed dependencies
├── .python-version               # 3.11
├── .streamlit/
│   └── config.toml               # Dark theme config
├── pages/
│   ├── 1_Upload.py               # Statement upload & column mapping
│   ├── 2_Reconcile.py            # Transfer/CC payment/loan reconciliation
│   ├── 3_Categorise.py           # Category assignment & overrides
│   ├── 4_Dashboard.py            # Charts, KPIs, insights
│   └── 5_Settings.py             # LLM config, budgets, data management
├── core/
│   ├── database.py               # SQLAlchemy schema & CRUD
│   ├── ingestion.py              # File parsers + column mapping
│   ├── reconciliation.py         # Matching engines
│   ├── categorisation.py         # Fuzzy + LLM categorisation pipeline
│   └── llm.py                    # Multi-provider LLM abstraction
├── data/
│   ├── categories.json           # Pre-seeded category/keyword map
│   └── finance.db                # SQLite (auto-created, gitignored)
└── README.md
```

## Configuration

### LLM (Optional)

Go to **Settings → LLM Configuration** and select a provider:
- **None** (default) — keyword/fuzzy matching only
- **Anthropic Claude** — model: `claude-sonnet-4-20250514`
- **OpenAI** — model: `gpt-4o-mini`
- **Groq** — model: `llama3-70b-8192` (free tier available)

API keys are stored only in your session and optionally in a local `.env` file. They are never written to the database.

### Environment Variables (.env)

```env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
```

## Technology Stack

| Layer | Technology |
|-------|-----------|
| UI | Streamlit 1.x |
| Charts | Plotly |
| Database | SQLite via SQLAlchemy |
| Fuzzy matching | rapidfuzz, thefuzz |
| PDF parsing | pdfplumber |
| LLM | anthropic / openai / groq SDKs |
| Package management | uv |

## Design Decisions

1. **Permanent SQLite storage** — the database persists across restarts so you can upload once and return later.
2. **Soft-deletes** — all deletes are initially soft (`is_deleted=1`); a separate purge step is required to remove data permanently.
3. **Reconciliation excludes from spend** — any transaction in a reconciled pair is excluded from expense calculations on the Dashboard.
4. **Column mapping profiles** — saved per `account_name` so repeat uploads require no re-mapping.
5. **LLM is entirely optional** — if disabled, categorisation falls back to keyword → fuzzy → Uncategorised.

## Development

```bash
uv sync          # install/update deps
uv run streamlit run app.py   # start dev server
```
