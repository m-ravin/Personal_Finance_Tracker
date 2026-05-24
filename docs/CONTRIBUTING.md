# Contributing Guide

## Prerequisites

- Python 3.11 (managed by uv, or supply your own interpreter)
- [uv](https://docs.astral.sh/uv/) package manager

## Setup

```bash
git clone <repo-url>
cd Personal_Finance_Tracker

# Install dependencies
uv sync

# Copy environment template
cp .env.example .env
# Edit .env and add API keys if you want LLM categorisation (optional)

# Start the app
uv run streamlit run app.py
```

> If `uv run` fails because uv cannot download its managed Python (e.g. firewall), supply your own:
> ```bash
> uv venv --python /path/to/python3.11
> python3.11 -m pip install --target .venv/Lib/site-packages <all-packages>
> .venv/Scripts/python.exe -m streamlit run app.py
> ```

## Running Tests

<!-- AUTO-GENERATED from tests/ -->
| Command | Scope |
|---------|-------|
| `.venv\Scripts\python.exe -m pytest tests/ -v` | Full suite (89 tests) |
| `.venv\Scripts\python.exe -m pytest tests/test_ingestion_sign.py` | CC sign-logic unit tests |
| `.venv\Scripts\python.exe -m pytest tests/test_sample_files.py` | Real-file integration (HSBC + CC CSV) |
| `.venv\Scripts\python.exe -m pytest tests/test_dashboard_date_range.py` | Dashboard date-range clamping |
| `.venv\Scripts\python.exe -m pytest tests/test_llm_categorisation.py` | LLM pipeline (Tier 2 skipped without API keys) |
<!-- END AUTO-GENERATED -->

### Writing new tests

- Place files in `tests/test_<feature>.py`
- Use `pytest.approx` for float comparisons
- Mock `core.database.get_active_llm_settings` — it is locally imported inside `categorise_with_llm`, so patching at `core.llm` will not work
- Mock `core.llm.categorise_with_llm` — same local-import reason applies for the pipeline tests

## Environment Variables

<!-- AUTO-GENERATED from .env.example -->
| Variable | Required | Description | Provider |
|----------|----------|-------------|----------|
| `ANTHROPIC_API_KEY` | No | Enables Claude categorisation | Anthropic |
| `OPENAI_API_KEY` | No | Enables GPT-4o-mini categorisation | OpenAI |
| `GROQ_API_KEY` | No | Enables Llama3-70b categorisation (free tier) | Groq |
<!-- END AUTO-GENERATED -->

All three are optional. Without them the app uses keyword + fuzzy matching only.

## Key Conventions

- **Never hard-delete** transactions — use the Settings → Data Management purge path only; `delete_by_source_file()` is the one exception and soft-deletes only
- **`final_category`** is the user override; `category` is the auto-detected value. Both must be preserved
- **`st.set_page_config()`** must be the first Streamlit call in every page file
- **`categorisation.reload_categories()`** must be called after any write to `data/categories.json`
- All DB-level tests should use in-memory data (pandas DataFrames), not the live `data/finance.db`
- **Date parsing**: always use `try_parse_date` from `ingestion.py`; never call `pd.to_datetime` directly for user-supplied date strings — `dayfirst=True` is not strict and will mis-parse DD/MM/YYYY dates

## PR Checklist

- [ ] `pytest tests/ -v` passes with no new failures
- [ ] Any new feature has a corresponding test in `tests/`
- [ ] `data/categories.json` changes are reflected in a test
- [ ] No API keys or secrets committed (check `.gitignore` covers `.env`)
