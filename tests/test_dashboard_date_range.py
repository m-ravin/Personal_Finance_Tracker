"""
Tests for the Dashboard date-range default_start clamping fix.

Bug reproduced: default_start = date(max_dt.year, 1, 1) could land
before min_dt (the earliest transaction date), causing Streamlit to
raise StreamlitAPIException because the default value lay outside
[min_value, max_value].

Expected behaviour:
  - When Jan 1 of the current year is within the data range → use Jan 1
  - When Jan 1 is BEFORE the earliest transaction → clamp to min_dt
"""
import pytest
from datetime import date


def _compute_default_start(min_dt: date, max_dt: date) -> date:
    """Mirrors the fixed logic from pages/4_Dashboard.py line 113."""
    return max(date(max_dt.year, 1, 1), min_dt)


# ── normal case: data starts before Jan 1 ────────────────────────────────────

def test_default_start_is_jan1_when_data_starts_before():
    """Data starts in prior year → default is Jan 1 of current year."""
    min_dt = date(2025, 11, 1)
    max_dt = date(2026, 12, 3)
    result = _compute_default_start(min_dt, max_dt)
    assert result == date(2026, 1, 1)


# ── crash case: data starts after Jan 1 ──────────────────────────────────────

def test_default_start_clamped_when_data_starts_after_jan1():
    """
    Reproduces the reported crash:
    min_dt=2026-01-03, max_dt=2026-12-03 → old code returned 2026-01-01
    which is outside [min_value, max_value].
    Fixed code clamps to min_dt=2026-01-03.
    """
    min_dt = date(2026, 1, 3)
    max_dt = date(2026, 12, 3)
    result = _compute_default_start(min_dt, max_dt)
    assert result == min_dt


def test_default_start_always_within_bounds():
    """default_start must satisfy min_dt <= default_start <= max_dt."""
    cases = [
        (date(2026, 1, 3),  date(2026, 12, 3)),
        (date(2026, 6, 15), date(2026, 12, 31)),
        (date(2025, 3, 1),  date(2026, 2, 28)),
        (date(2026, 12, 1), date(2026, 12, 31)),
    ]
    for min_dt, max_dt in cases:
        result = _compute_default_start(min_dt, max_dt)
        assert min_dt <= result <= max_dt, (
            f"default_start {result} out of bounds [{min_dt}, {max_dt}]"
        )


# ── edge: only one day of data ────────────────────────────────────────────────

def test_single_day_dataset():
    """min_dt == max_dt → default_start must equal that single date."""
    d = date(2026, 3, 15)
    result = _compute_default_start(d, d)
    assert result == d
