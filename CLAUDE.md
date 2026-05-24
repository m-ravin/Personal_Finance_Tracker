# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                          # install / update dependencies
uv run streamlit run app.py      # start dev server at http://localhost:8501
uv add <package>                 # add a new dependency
```

> **Venv note**: If `uv run` errors with "No Python at …", the uv-managed interpreter was removed. Use `.venv\Scripts\python.exe` directly, or rebuild with `uv venv --python <path>` then install deps via pip into the venv's site-packages.

### Tests

```bash
# Run full suite (89 tests)
.venv\Scripts\python.exe -m pytest tests/ -v

# Individual test files
.venv\Scripts\python.exe -m pytest tests/test_ingestion_sign.py        # CC sign-logic unit tests
.venv\Scripts\python.exe -m pytest tests/test_sample_files.py           # real-file integration (HSBC + CC)
.venv\Scripts\python.exe -m pytest tests/test_dashboard_date_range.py  # date-range clamping
.venv\Scripts\python.exe -m pytest tests/test_llm_categorisation.py    # LLM tests (Tier 2 skipped unless API keys set)
```

Tier 2 LLM tests activate automatically when API keys are present in `.env` (see Environment variables below).

## Architecture

This is a **multi-page Streamlit app** with a clear pipeline: Upload → Reconcile → Categorise → Dashboard.

### Entry point
`app.py` — sets page config, calls `init_db()`, and renders the sidebar nav. **`st.set_page_config()` must be the very first Streamlit call** in any page file; violating this crashes the app.

### Core modules (`core/`)

| Module | Responsibility |
|--------|---------------|
| `database.py` | SQLAlchemy schema + all CRUD. Single SQLite file at `data/finance.db`. WAL mode enabled. Two main tables: `transactions` and `reconciliation_pairs`. Key helpers: `upsert_transactions`, `delete_by_source_file` (soft-delete all rows for a file, used by the overwrite-on-reupload flow). |
| `ingestion.py` | CSV/XLSX/PDF parsers + column auto-mapping. Saves mapping profiles per `account_name` so repeat uploads need no reconfiguration. `try_parse_date` tries explicit `strptime` formats (DD/MM/YYYY first) before falling back to `pd.to_datetime` — avoids ambiguous month/day swaps. |
| `reconciliation.py` | Three matching engines: internal transfers (±3 days, 0.5% tolerance), CC payments (±5 days), and personal loans (fuzzy contact matching ≥ 80). |
| `categorisation.py` | Four-step pipeline: DB override → exact keyword match → rapidfuzz token_sort_ratio ≥ 75 → LLM → "Uncategorised". Categories cached in module globals; call `reload_categories()` after any write. |
| `llm.py` | Multi-provider abstraction (Anthropic / OpenAI / Groq). LLM is entirely optional; keyword+fuzzy is the default path. |
| `ui_helpers.py` | Sidebar stats widget and CSV export buttons shared across pages. |

### Data flow & key conventions

- **Soft-deletes only**: `is_deleted=1` flag; a separate purge step removes rows permanently. Never hard-delete without going through the Settings page purge path.
- **`final_category` vs `category`**: `category` is the auto-detected value; `final_category` is the user override. Dashboard always resolves `COALESCE(final_category, category)`.
- **Reconciliation exclusion**: Approved pairs set `reconciliation_status = 'approved'` on both transactions. Dashboard filters these out by default.
- **CC sign-column logic**: For CC statements with a single amount column, three-layer detection applies: auto-map → CC heuristic scan → fallback. If a sign column is present and contains `CR`/`CREDIT` → credit; `DR`/`DEBIT` → debit. If no sign column (or the column holds non-CR/DR values like "Chip and PIN"), the raw amount sign is used: positive → credit, negative → debit.
- **Column mapping profiles**: Persisted per `account_name` in the DB. Re-uploading the same account reuses the saved profile; new columns (e.g. sign column) trigger auto-correction.
- **Overwrite on re-upload**: The Upload page has an "♻️ Overwrite existing data from this file" checkbox. When checked, `delete_by_source_file(filename)` soft-deletes all existing rows for that file before re-inserting, allowing corrected or extended statements to replace stale data cleanly.
- **Date parsing**: `try_parse_date` in `ingestion.py` always tries the explicit format list (`%d/%m/%Y` before `%m/%d/%Y`) first. `pd.to_datetime(dayfirst=True)` is only a hint and will mis-parse ambiguous dates like `12/03/2026` as December 3 if reached first.

### Categories

`data/categories.json` — 21 pre-seeded categories with keyword lists. Edit this file directly or use **Settings → Categories** in the UI. After programmatic writes, call `categorisation.reload_categories()` to bust the module-level cache.

### Environment variables (`.env`)

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
```

API keys are loaded from `.env` at startup and stored in Streamlit session state. They are never written to the database.

### Streamlit session state patterns

Pages use `st.session_state` keys prefixed by feature (e.g. `drill_category`, `drill_month`). The Dashboard drill-through state is cleared by a sidebar button that deletes these keys.
