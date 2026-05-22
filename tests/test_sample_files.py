"""
Integration tests using the two real sample CSV files provided by the user.

Files under test:
  1. HSBC march_transactions.csv     — bank account, separate Debit/Credit cols
  2. Transactions 31-01-2026 - 07-05-2026.csv — CC, single Amount col with +/-

Coverage:
  - Column auto-detection (auto_detect_mapping)
  - Debit/credit assignment for both file formats
  - CC sign logic: positive → credit, negative → debit (no explicit CR/DR column)
  - Categorisation pipeline (no LLM)
"""
import pytest
from core.ingestion import load_csv, auto_detect_mapping, apply_mapping
from core.categorisation import categorise_transaction, exact_match

# ─────────────────────────────────────────────────────────────────────────────
# Sample file content (embedded so tests are self-contained)
# ─────────────────────────────────────────────────────────────────────────────

HSBC_CSV = b"""Date,Description,Debit,Credit
28/02/2026,BALANCE BROUGHT FORWARD,,549.11
01/03/2026,BP Saidalavi kuttampa Card,536.06,
01/03/2026,BP Saidalavi kuttampa Card,,13.05
05/03/2026,CR Roofoods Limited,,166.07
05/03/2026,CR Roofoods Limited,,179.12
06/03/2026,CR Just Eat.co.uk Lim JEA21386763-576539,,94.39
06/03/2026,CR Just Eat.co.uk Lim JEA21386763-576539,,273.51
11/03/2026,BP Fresh and fresh lo 41880,41.80,
11/03/2026,BP Fresh and fresh lo 41880,,0.00
11/03/2026,BP Fresh and fresh lo 42075,66.60,
11/03/2026,BP Fresh and fresh lo 42075,,0.00
11/03/2026,BP Fresh and fresh lo 42291,66.75,
11/03/2026,BP Fresh and fresh lo 42291,,0.00
11/03/2026,BP Fresh and fresh lo 42493,31.50,
11/03/2026,BP Fresh and fresh lo 42493,,0.00
11/03/2026,BP Fresh and fresh lo 42806,19.10,
11/03/2026,BP Fresh and fresh lo 42806,,47.76
12/03/2026,CR Roofoods Limited,,226.31
12/03/2026,CR Roofoods Limited,,274.07
13/03/2026,CR Just Eat.co.uk Lim JEA21437979-576558,,87.19
13/03/2026,CR Just Eat.co.uk Lim JEA21437979-576558,,361.26
16/03/2026,DD CASTLE WATER LTD,119.59,
16/03/2026,DD CASTLE WATER LTD,,241.67
19/03/2026,CR BAVAS KEBAB LTD Sent from myPOS,,531.00
19/03/2026,CR Roofoods Limited,,161.97
19/03/2026,CR Roofoods Limited,,934.64
20/03/2026,CR Just Eat.co.uk Lim JEA21495669-576587,,50.50
20/03/2026,CR Just Eat.co.uk Lim JEA21495669-576587,,985.14
21/03/2026,DR TOTAL CHARGES TO 27FEB2026,1.75,
21/03/2026,DR TOTAL CHARGES TO 27FEB2026,,983.39
26/03/2026,CR Roofoods Limited,,91.10
26/03/2026,CR Roofoods Limited,,1074.49
27/03/2026,CR Just Eat.co.uk Lim JEA21587584-576612,,52.82
27/03/2026,CR Just Eat.co.uk Lim JEA21587584-576612,,1127.31
29/03/2026,BALANCE CARRIED FORWARD,,1127.31
"""

CC_CSV = b"""Clearance Date,Authorisation Date,Description,Amount,Original Amount,Original Currency,Merchant Name,Card Ending,Cardholder Name,Card Name,Transaction Type,Category,Has Receipts,Note
07/05/2026,06/05/2026,UNIVERSAL EXPRESS DIST - Isleworth - Card Ending: 2183,357.88,357.88, GBP,UNIVERSAL EXPRESS DIST,2183,Saidalavi Kuttampalli,,Over the phone,Retail,No,
06/05/2026,06/05/2026,Payment made (VirtualBankTransfer),-400.00,-400.00, ,,,,,Other,Inbound payment,No,
06/05/2026,05/05/2026,Exel Foods Limited - London - Card Ending: 2183,2000.00,2000.00, GBP,Exel Foods Limited,2183,Saidalavi Kuttampalli,,Over the phone,Retail,No,
05/05/2026,05/05/2026,Payment made (VirtualBankTransfer),-200.00,-200.00, ,,,,,Other,Inbound payment,No,
03/05/2026,02/05/2026,BOOKER LTD - 38588424 - WELLINGBOROUG - Card Ending: 2183,218.12,218.12, GBP,BOOKER LTD - 38588424,2183,Saidalavi Kuttampalli,,Chip and PIN,Retail,No,
02/05/2026,02/05/2026,Interest Charge (03/04/2026 - 02/05/2026),309.82,, ,,,,,Other,General,No,
28/04/2026,27/04/2026,UNIVERSAL EXPRESS DIST - Isleworth - Card Ending: 2183,549.87,549.87, GBP,UNIVERSAL EXPRESS DIST,2183,Saidalavi Kuttampalli,,Chip and PIN,Retail,No,
12/03/2026,11/03/2026,WWW.LBHF.GOV.UK - LONDON - Card Ending: 2183,97.60,97.60, GBP,WWW.LBHF.GOV.UK,2183,Saidalavi Kuttampalli,,Online,Services,No,
10/03/2026,10/03/2026,Card Interest Charge (03/02/2026 - 02/03/2026),74.49,, ,,,,,Other,General,No,
07/03/2026,06/03/2026,Madina Butchers - London - Card Ending: 2183,71.33,71.33, GBP,Madina Butchers,2183,Saidalavi Kuttampalli,,Contactless,General,No,
09/03/2026,09/03/2026,Payment made (Direct Debit),-346.94,-346.94, ,,,,,Other,Inbound payment,No,
"""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_and_map(csv_bytes: bytes, account_name: str, account_type: str):
    df = load_csv(csv_bytes, "test.csv")
    mapping = auto_detect_mapping(list(df.columns))
    valid, failed = apply_mapping(
        df, mapping, account_name=account_name,
        account_type=account_type, source_file="test.csv",
    )
    return df, mapping, valid, failed


def _by_desc(rows, fragment: str):
    frag = fragment.upper()
    return [r for r in rows if frag in r["description"].upper()]


# ─────────────────────────────────────────────────────────────────────────────
# HSBC — column auto-detection
# ─────────────────────────────────────────────────────────────────────────────

class TestHSBCColumnMapping:
    def setup_method(self):
        self.df = load_csv(HSBC_CSV, "hsbc.csv")
        self.mapping = auto_detect_mapping(list(self.df.columns))

    def test_date_column_detected(self):
        assert self.mapping.get("Date") == "date"

    def test_description_column_detected(self):
        assert self.mapping.get("Description") == "description"

    def test_debit_column_detected(self):
        assert self.mapping.get("Debit") == "debit"

    def test_credit_column_detected(self):
        assert self.mapping.get("Credit") == "credit"

    def test_no_spurious_amount_mapping(self):
        assert "amount" not in self.mapping.values()


# ─────────────────────────────────────────────────────────────────────────────
# HSBC — debit / credit assignment
# ─────────────────────────────────────────────────────────────────────────────

class TestHSBCAmounts:
    def setup_method(self):
        _, _, self.valid, self.failed = _load_and_map(
            HSBC_CSV, "HSBC Business", "bank"
        )

    def test_no_failed_rows(self):
        assert self.failed == [], f"Unexpected failures: {self.failed}"

    def test_balance_brought_forward_is_credit(self):
        rows = _by_desc(self.valid, "BALANCE BROUGHT FORWARD")
        assert len(rows) == 1
        assert rows[0]["credit"] == pytest.approx(549.11)
        assert rows[0]["debit"] is None

    def test_balance_carried_forward_is_credit(self):
        rows = _by_desc(self.valid, "BALANCE CARRIED FORWARD")
        assert len(rows) == 1
        assert rows[0]["credit"] == pytest.approx(1127.31)
        assert rows[0]["debit"] is None

    def test_bp_debit_row(self):
        rows = _by_desc(self.valid, "BP Saidalavi kuttampa Card")
        assert any(r["debit"] == pytest.approx(536.06) for r in rows)

    def test_bp_credit_row(self):
        rows = _by_desc(self.valid, "BP Saidalavi kuttampa Card")
        assert any(r["credit"] == pytest.approx(13.05) for r in rows)

    def test_cr_roofoods_are_credits(self):
        rows = _by_desc(self.valid, "CR Roofoods Limited")
        assert len(rows) == 8
        for r in rows:
            assert r["credit"] is not None and r["credit"] >= 0
            assert r["debit"] is None

    def test_just_eat_rows_are_credits(self):
        rows = _by_desc(self.valid, "CR Just Eat")
        assert len(rows) == 8
        for r in rows:
            assert r["credit"] is not None and r["credit"] > 0
            assert r["debit"] is None

    def test_dd_castle_water_debit_row(self):
        rows = _by_desc(self.valid, "DD CASTLE WATER LTD")
        assert any(r["debit"] == pytest.approx(119.59) for r in rows)

    def test_dd_castle_water_credit_row(self):
        rows = _by_desc(self.valid, "DD CASTLE WATER LTD")
        assert any(r["credit"] == pytest.approx(241.67) for r in rows)

    def test_total_charges_debit(self):
        rows = _by_desc(self.valid, "DR TOTAL CHARGES")
        assert any(r["debit"] == pytest.approx(1.75) for r in rows)

    def test_zero_credit_row_parsed(self):
        rows = _by_desc(self.valid, "BP Fresh and fresh lo 41880")
        assert any(r["credit"] == pytest.approx(0.0) for r in rows)

    def test_net_amount_integrity(self):
        for r in self.valid:
            expected = (r["credit"] or 0.0) - (r["debit"] or 0.0)
            assert r["net_amount"] == pytest.approx(expected), r["description"]


# ─────────────────────────────────────────────────────────────────────────────
# CC — column auto-detection
# ─────────────────────────────────────────────────────────────────────────────

class TestCCColumnMapping:
    def setup_method(self):
        self.df = load_csv(CC_CSV, "cc.csv")
        self.mapping = auto_detect_mapping(list(self.df.columns))

    def test_clearance_date_mapped_to_date(self):
        assert self.mapping.get("Clearance Date") == "date"

    def test_description_mapped(self):
        assert self.mapping.get("Description") == "description"

    def test_amount_column_mapped(self):
        assert self.mapping.get("Amount") == "amount"

    def test_original_amount_not_mapped_as_amount(self):
        assert self.mapping.get("Original Amount") != "amount"

    def test_transaction_type_mapped(self):
        assert self.mapping.get("Transaction Type") == "transaction_type"

    def test_no_separate_debit_credit_mapped(self):
        assert "debit" not in self.mapping.values()
        assert "credit" not in self.mapping.values()


# ─────────────────────────────────────────────────────────────────────────────
# CC — sign handling: positive → credit, negative → debit
# ─────────────────────────────────────────────────────────────────────────────

class TestCCSignHandling:
    def setup_method(self):
        _, _, self.valid, self.failed = _load_and_map(
            CC_CSV, "Business CC", "credit_card"
        )

    def test_no_failed_rows(self):
        assert self.failed == [], f"Unexpected failures: {self.failed}"

    def test_positive_purchase_becomes_credit(self):
        rows = _by_desc(self.valid, "UNIVERSAL EXPRESS DIST")
        row = next(r for r in rows if r["credit"] == pytest.approx(357.88))
        assert row["debit"] is None
        assert row["net_amount"] == pytest.approx(357.88)

    def test_negative_payment_becomes_debit(self):
        rows = _by_desc(self.valid, "Payment made (VirtualBankTransfer)")
        row = next(r for r in rows if r["debit"] == pytest.approx(400.00))
        assert row["credit"] is None
        assert row["net_amount"] == pytest.approx(-400.00)

    def test_booker_positive_is_credit(self):
        rows = _by_desc(self.valid, "BOOKER LTD")
        assert len(rows) == 1
        assert rows[0]["credit"] == pytest.approx(218.12)
        assert rows[0]["debit"] is None

    def test_exel_foods_positive_is_credit(self):
        rows = _by_desc(self.valid, "Exel Foods Limited")
        assert len(rows) == 1
        assert rows[0]["credit"] == pytest.approx(2000.00)
        assert rows[0]["debit"] is None

    def test_interest_charge_positive_is_credit(self):
        rows = _by_desc(self.valid, "Interest Charge (03/04/2026")
        assert len(rows) == 1
        assert rows[0]["credit"] == pytest.approx(309.82)
        assert rows[0]["debit"] is None

    def test_direct_debit_payment_negative_is_debit(self):
        rows = _by_desc(self.valid, "Payment made (Direct Debit)")
        assert len(rows) == 1
        assert rows[0]["debit"] == pytest.approx(346.94)
        assert rows[0]["credit"] is None

    def test_all_positive_amounts_produce_credit(self):
        for r in self.valid:
            if r["net_amount"] > 0:
                assert r["credit"] is not None and r["debit"] is None, (
                    f"Expected credit for positive row: {r['description']}"
                )

    def test_all_negative_amounts_produce_debit(self):
        for r in self.valid:
            if r["net_amount"] < 0:
                assert r["debit"] is not None and r["credit"] is None, (
                    f"Expected debit for negative row: {r['description']}"
                )

    def test_net_amount_integrity(self):
        for r in self.valid:
            expected = (r["credit"] or 0.0) - (r["debit"] or 0.0)
            assert r["net_amount"] == pytest.approx(expected), r["description"]


# ─────────────────────────────────────────────────────────────────────────────
# Categorisation
# ─────────────────────────────────────────────────────────────────────────────

class TestCategorisation:

    # ── Exact keyword hits ────────────────────────────────────────────────────

    def test_interest_charge_is_financial(self):
        result = categorise_transaction(
            "Interest Charge (03/04/2026 - 02/05/2026)", use_llm=False
        )
        assert result["category"] == "Financial"
        assert result["subcategory"] == "Interest"
        assert result["method"] == "exact"

    def test_card_interest_charge_is_financial(self):
        result = categorise_transaction(
            "Card Interest Charge (03/02/2026 - 02/03/2026)", use_llm=False
        )
        assert result["category"] == "Financial"
        assert result["subcategory"] == "Interest"

    # ── No exact keyword match ────────────────────────────────────────────────

    def test_roofoods_no_exact_match(self):
        assert exact_match("CR Roofoods Limited") is None

    def test_just_eat_no_exact_match(self):
        assert exact_match("CR Just Eat.co.uk Lim JEA21386763-576539") is None

    def test_booker_no_exact_match(self):
        assert exact_match(
            "BOOKER LTD - 38588424 - WELLINGBOROUG - Card Ending: 2183"
        ) is None

    def test_bestway_no_exact_match(self):
        assert exact_match(
            "BESTWAY WHOLESALE - GREAT BRITAI - Card Ending: 2183"
        ) is None

    def test_universal_express_no_exact_match(self):
        assert exact_match(
            "UNIVERSAL EXPRESS DIST - Isleworth - Card Ending: 2183"
        ) is None

    def test_payment_made_no_exact_match(self):
        assert exact_match("Payment made (VirtualBankTransfer)") is None

    def test_castle_water_no_exact_match(self):
        # 'WATER BILL' keyword is not a substring of 'DD CASTLE WATER LTD'
        assert exact_match("DD CASTLE WATER LTD") is None

    # ── Full pipeline returns required structure ───────────────────────────────

    def test_categorise_always_returns_required_keys(self):
        descriptions = [
            "Interest Charge (03/04/2026 - 02/05/2026)",
            "CR Roofoods Limited",
            "BOOKER LTD - 38588424 - WELLINGBOROUG",
            "Payment made (VirtualBankTransfer)",
            "DD CASTLE WATER LTD",
            "BP Saidalavi kuttampa Card",
        ]
        required = {"category", "subcategory", "type_tag", "confidence", "method"}
        for desc in descriptions:
            result = categorise_transaction(desc, use_llm=False)
            assert required.issubset(result.keys()), (
                f"Missing keys in result for: {desc}"
            )

    def test_unknown_merchants_fall_back_gracefully(self):
        """Merchants not in categories.json must not raise; they get Uncategorised."""
        for desc in [
            "BOOKER LTD - 38588424 - WELLINGBOROUG",
            "BESTWAY WHOLESALE - GREAT BRITAI",
            "UNIVERSAL EXPRESS DIST - Isleworth",
            "Madina Butchers - London",
            "Exel Foods Limited - London",
        ]:
            result = categorise_transaction(desc, use_llm=False)
            assert "category" in result  # must not crash
