"""Tests for fuel-tax-doctor."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fuel_tax_doctor as ftd

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "examples")

AS_OF = datetime(2026, 11, 18, tzinfo=timezone.utc)

HEADER = ("jurisdiction,miles,gallons_purchased,tax_paid_gallons,"
          "quarter,credit\n")

RATES_HEADER = "jurisdiction,rate\n"

RATES = (RATES_HEADER
         + "TX,0.20\n"
         + "OK,0.19\n"
         + "NM,0.21\n"
         + "WY,0.24\n")


def write_file(content, suffix=".csv"):
    """Write content to a temp file and return its path."""
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=suffix, delete=False, encoding="utf-8")
    handle.write(content)
    handle.close()
    return handle.name


def row(jurisdiction="TX", miles="6000", gallons="1000",
        tax_paid="1000", quarter="2026-Q3", credit=""):
    """Build one CSV row with the standard columns."""
    return ",".join([jurisdiction, miles, gallons, tax_paid,
                     quarter, credit]) + "\n"


def parse(content, as_of=AS_OF, **kwargs):
    """Parse CSV content and return (rows, findings, total)."""
    return ftd.parse_csv(write_file(content), as_of, **kwargs)


def audit(content, as_of=AS_OF, rates=None, **kwargs):
    """Parse CSV content and audit it, returning all findings."""
    rows, findings, total = parse(content, as_of)
    findings.extend(ftd.audit(rows, as_of, rates=rates,
                              declared_total_miles=total, **kwargs))
    return rows, findings


def rules(findings):
    """Return the sorted rule names of a findings list."""
    return sorted(item.rule for item in findings)


def by_rule(findings, rule):
    """Return the findings with one rule name."""
    return [item for item in findings if item.rule == rule]


def load_rates(content):
    """Load rates from CSV content and return (rates, findings)."""
    return ftd.load_rates(write_file(content))


class TestQuarterParsing(unittest.TestCase):
    """Quarter string parsing."""

    def test_parse_quarter_dash(self):
        self.assertEqual(ftd.parse_quarter("2026-Q3"), (2026, 3))

    def test_parse_quarter_no_separator(self):
        self.assertEqual(ftd.parse_quarter("2026Q3"), (2026, 3))

    def test_parse_quarter_space(self):
        self.assertEqual(ftd.parse_quarter("2026 Q3"), (2026, 3))

    def test_parse_quarter_reversed(self):
        self.assertEqual(ftd.parse_quarter("Q3 2026"), (2026, 3))

    def test_parse_quarter_reversed_dash(self):
        self.assertEqual(ftd.parse_quarter("Q4-2026"), (2026, 4))

    def test_parse_quarter_invalid(self):
        self.assertIsNone(ftd.parse_quarter("QX"))
        self.assertIsNone(ftd.parse_quarter("2026-Q5"))
        self.assertIsNone(ftd.parse_quarter("2026"))
        self.assertIsNone(ftd.parse_quarter(""))
        self.assertIsNone(ftd.parse_quarter(None))

    def test_quarter_end(self):
        self.assertEqual(ftd.quarter_end(2026, 1), date(2026, 3, 31))
        self.assertEqual(ftd.quarter_end(2026, 2), date(2026, 6, 30))
        self.assertEqual(ftd.quarter_end(2026, 3), date(2026, 9, 30))
        self.assertEqual(ftd.quarter_end(2026, 4), date(2026, 12, 31))

    def test_format_quarter(self):
        self.assertEqual(ftd.format_quarter(2026, 3), "2026-Q3")


class TestNumberParsing(unittest.TestCase):
    """Number parsing."""

    def test_parse_number_plain(self):
        self.assertEqual(ftd.parse_number("1200.5"),
                         (Decimal("1200.5"), True, False))

    def test_parse_number_commas_and_dollar(self):
        self.assertEqual(ftd.parse_number("$1,200.50"),
                         (Decimal("1200.50"), True, False))

    def test_parse_number_empty(self):
        self.assertEqual(ftd.parse_number(""),
                         (None, True, True))
        self.assertEqual(ftd.parse_number(None),
                         (None, True, True))

    def test_parse_number_invalid(self):
        self.assertEqual(ftd.parse_number("abc"),
                         (None, False, False))

    def test_parse_number_negative(self):
        self.assertEqual(ftd.parse_number("-50"),
                         (None, False, False))


class TestCsvParsing(unittest.TestCase):
    """CSV parsing, aliases, and bad data."""

    def test_parse_clean_row(self):
        rows, findings, total = parse(HEADER + row())
        self.assertEqual(len(rows), 1)
        self.assertEqual(findings, [])
        self.assertIsNone(total)
        self.assertEqual(rows[0]["jurisdiction"], "TX")
        self.assertEqual(rows[0]["miles"], Decimal("6000"))
        self.assertEqual(rows[0]["gallons"], Decimal("1000"))
        self.assertEqual(rows[0]["tax_paid"], Decimal("1000"))
        self.assertEqual(rows[0]["quarter"], (2026, 3))
        self.assertFalse(rows[0]["credit"])

    def test_parse_credit_flag(self):
        rows, _findings, _total = parse(HEADER + row(credit="yes"))
        self.assertTrue(rows[0]["credit"])
        rows, _findings, _total = parse(HEADER + row(credit="TRUE"))
        self.assertTrue(rows[0]["credit"])
        rows, _findings, _total = parse(HEADER + row(credit="no"))
        self.assertFalse(rows[0]["credit"])

    def test_parse_header_aliases(self):
        content = ("state,total_miles,gallons,tax_paid,period\n"
                   "TX,6000,1000,1000,2026-Q3\n")
        rows, findings, _total = parse(content)
        self.assertEqual(len(rows), 1)
        self.assertEqual(findings, [])
        self.assertEqual(rows[0]["miles"], Decimal("6000"))

    def test_parse_bom_header(self):
        rows, findings, _total = parse(
            "\ufeff" + HEADER + row())
        self.assertEqual(len(rows), 1)
        self.assertEqual(findings, [])

    def test_parse_total_row(self):
        content = HEADER + row() + "TOTAL,6000,,,,\n"
        rows, findings, total = parse(content)
        self.assertEqual(len(rows), 1)
        self.assertEqual(findings, [])
        self.assertEqual(total, Decimal("6000"))

    def test_parse_total_row_invalid_miles(self):
        content = HEADER + row() + "TOTAL,abc,,,,\n"
        _rows, findings, total = parse(content)
        self.assertIsNone(total)
        self.assertEqual(rules(findings), ["invalid-amount"])

    def test_parse_total_row_flag_wins(self):
        content = HEADER + row() + "TOTAL,6000,,,,\n"
        _rows, _findings, total = parse(
            content, total_miles_arg=Decimal("7000"))
        self.assertEqual(total, Decimal("7000"))

    def test_parse_malformed_row(self):
        content = HEADER + "TX,6000,1000\n" + row()
        rows, findings, _total = parse(content)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rules(findings), ["malformed-row"])

    def test_parse_empty_jurisdiction(self):
        content = HEADER + row(jurisdiction="") + row()
        rows, findings, _total = parse(content)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rules(findings), ["empty-jurisdiction"])

    def test_parse_invalid_amounts(self):
        content = HEADER + row(miles="abc") \
            + row("OK", gallons="-5")
        _rows, findings, _total = parse(content)
        self.assertEqual(rules(findings),
                         ["invalid-amount", "invalid-amount"])

    def test_parse_invalid_quarter(self):
        _rows, findings, _total = parse(HEADER + row(quarter="QX"))
        self.assertEqual(rules(findings), ["invalid-quarter"])

    def test_parse_duplicate_jurisdiction(self):
        content = HEADER + row() + row()
        _rows, findings, _total = parse(content)
        self.assertEqual(rules(findings), ["duplicate-jurisdiction"])

    def test_parse_duplicate_different_quarter_ok(self):
        content = HEADER + row() + row(quarter="2026-Q2")
        _rows, findings, _total = parse(content)
        self.assertEqual(findings, [])

    def test_parse_future_quarter(self):
        _rows, findings, _total = parse(HEADER + row(quarter="2026-Q4"))
        self.assertEqual(rules(findings), ["future-quarter"])

    def test_parse_missing_file(self):
        with self.assertRaises(ValueError):
            ftd.parse_csv("/nonexistent/fuel-tax.csv", AS_OF)

    def test_parse_empty_file(self):
        with self.assertRaises(ValueError):
            ftd.parse_csv(write_file(""), AS_OF)

    def test_parse_no_jurisdiction_column(self):
        with self.assertRaises(ValueError):
            ftd.parse_csv(write_file("miles,gallons\n6000,1000\n"),
                          AS_OF)

    def test_parse_blank_lines_skipped(self):
        content = HEADER + "\n" + row() + "\n"
        rows, findings, _total = parse(content)
        self.assertEqual(len(rows), 1)
        self.assertEqual(findings, [])


class TestRatesFile(unittest.TestCase):
    """Rates file loading."""

    def test_load_rates(self):
        rates, findings = load_rates(RATES)
        self.assertEqual(findings, [])
        self.assertEqual(rates["TX"], Decimal("0.20"))
        self.assertEqual(rates["WY"], Decimal("0.24"))

    def test_load_rates_case_insensitive_keys(self):
        rates, _findings = load_rates(RATES_HEADER + "tx,0.20\n")
        self.assertIn("TX", rates)

    def test_load_rates_invalid_rate(self):
        rates, findings = load_rates(RATES_HEADER + "TX,abc\n")
        self.assertEqual(rates, {})
        self.assertEqual(rules(findings), ["invalid-rate"])

    def test_load_rates_empty_rate(self):
        rates, findings = load_rates(RATES_HEADER + "TX,\n")
        self.assertEqual(rates, {})
        self.assertEqual(rules(findings), ["invalid-rate"])

    def test_load_rates_malformed_row(self):
        rates, findings = load_rates(RATES_HEADER + "TX\n")
        self.assertEqual(rates, {})
        self.assertEqual(rules(findings), ["malformed-row"])

    def test_load_rates_missing_file(self):
        with self.assertRaises(ValueError):
            ftd.load_rates("/nonexistent/rates.csv")

    def test_load_rates_no_rate_column(self):
        with self.assertRaises(ValueError):
            ftd.load_rates(write_file("jurisdiction\nTX\n"))


class TestAuditRules(unittest.TestCase):
    """The audit rules."""

    def test_clean_rows_no_findings(self):
        content = HEADER + row() + row("OK", "3000", "500", "500")
        _rows, findings = audit(content)
        self.assertEqual(findings, [])

    def test_miles_no_fuel(self):
        _rows, findings = audit(HEADER + row(gallons="0",
                                             tax_paid="0"))
        self.assertEqual(rules(findings),
                         ["implausible-mpg", "miles-no-fuel"])

    def test_miles_no_fuel_empty_gallons(self):
        _rows, findings = audit(HEADER + row(gallons="",
                                             tax_paid=""))
        self.assertEqual(rules(findings),
                         ["implausible-mpg", "miles-no-fuel"])

    def test_fuel_no_miles(self):
        _rows, findings = audit(HEADER + row(miles="0"))
        self.assertEqual(rules(findings), ["fuel-no-miles"])

    def test_tax_paid_exceeds_purchased(self):
        _rows, findings = audit(HEADER + row(tax_paid="1200"))
        self.assertEqual(rules(findings),
                         ["negative-net-gallons",
                          "tax-paid-exceeds-purchased"])

    def test_mixed_quarters(self):
        content = HEADER + row() + row("OK", "3000", "500", "500",
                                       "2026-Q2")
        _rows, findings = audit(content)
        self.assertEqual(rules(findings), ["mixed-quarters"])

    def test_miles_total_mismatch(self):
        rows, findings, total = parse(HEADER + row()
                                      + "TOTAL,5000,,,,\n")
        findings.extend(ftd.audit(rows, AS_OF,
                                  declared_total_miles=total))
        self.assertEqual(rules(findings), ["miles-total-mismatch"])
        self.assertIn("1,000.00", by_rule(
            findings, "miles-total-mismatch")[0].message)

    def test_miles_total_match(self):
        rows, findings, total = parse(HEADER + row()
                                      + "TOTAL,6000,,,,\n")
        findings.extend(ftd.audit(rows, AS_OF,
                                  declared_total_miles=total))
        self.assertEqual(findings, [])

    def test_fleet_mpg_too_low(self):
        content = HEADER + row(miles="3000", gallons="1000",
                               tax_paid="1000")
        _rows, findings = audit(content)
        self.assertEqual(rules(findings),
                         ["implausible-jurisdiction-mpg",
                          "implausible-mpg"])
        self.assertEqual(by_rule(findings, "implausible-mpg")[0]
                         .jurisdiction, "fleet")

    def test_fleet_mpg_too_high(self):
        content = HEADER + row(miles="9000", gallons="1000",
                               tax_paid="1000")
        _rows, findings = audit(content)
        self.assertEqual(rules(findings),
                         ["implausible-jurisdiction-mpg",
                          "implausible-mpg"])

    def test_fleet_mpg_boundaries_pass(self):
        low = HEADER + row(miles="4000", gallons="1000",
                           tax_paid="1000")
        _rows, findings = audit(low)
        self.assertEqual(findings, [])
        high = HEADER + row(miles="8000", gallons="1000",
                            tax_paid="1000")
        _rows, findings = audit(high)
        self.assertEqual(findings, [])

    def test_fleet_mpg_custom_band(self):
        content = HEADER + row()
        _rows, findings = audit(content, min_mpg=Decimal("7"))
        self.assertEqual(rules(findings),
                         ["implausible-jurisdiction-mpg",
                          "implausible-mpg"])

    def test_fleet_mpg_no_gallons(self):
        content = HEADER + row(gallons="0", tax_paid="0")
        _rows, findings = audit(content)
        self.assertEqual(rules(findings),
                         ["implausible-mpg", "miles-no-fuel"])

    def test_jurisdiction_mpg_warning(self):
        content = HEADER + row() + row("SD", "600", "500", "480")
        _rows, findings = audit(content)
        self.assertEqual(rules(findings),
                         ["implausible-jurisdiction-mpg",
                          "negative-net-gallons"])

    def test_jurisdiction_mpg_boundary_passes(self):
        content = HEADER + row(miles="800", gallons="100",
                               tax_paid="0")
        _rows, findings = audit(content)
        self.assertEqual(findings, [])

    def test_negative_net_gallons_warning(self):
        content = HEADER + row(miles="2100", gallons="300",
                               tax_paid="600")
        _rows, findings = audit(content)
        self.assertIn("negative-net-gallons", rules(findings))

    def test_negative_net_flagged_as_credit_ok(self):
        content = HEADER + row(miles="2100", gallons="300",
                               tax_paid="600", credit="yes")
        _rows, findings = audit(content)
        self.assertEqual(rules(findings),
                         ["tax-paid-exceeds-purchased"])

    def test_credit_without_negative_net(self):
        content = HEADER + row(credit="yes")
        _rows, findings = audit(content)
        self.assertEqual(rules(findings),
                         ["credit-without-negative-net"])

    def test_missing_jurisdiction(self):
        rates, _findings = load_rates(RATES)
        _rows, findings = audit(HEADER + row(), rates=rates)
        self.assertEqual(rules(findings),
                         ["missing-jurisdiction"] * 3)
        missing = sorted(item.jurisdiction
                         for item in by_rule(
                             findings, "missing-jurisdiction"))
        self.assertEqual(missing, ["NM", "OK", "WY"])

    def test_missing_rate(self):
        rates, _findings = load_rates(RATES)
        content = HEADER + row("FL", "2600", "420", "100")
        _rows, findings = audit(content, rates=rates)
        self.assertEqual(rules(findings),
                         ["missing-jurisdiction",
                          "missing-jurisdiction",
                          "missing-jurisdiction",
                          "missing-jurisdiction", "missing-rate"])

    def test_no_rates_file_skips_rate_checks(self):
        content = HEADER + row("FL", "2600", "420", "100")
        _rows, findings = audit(content)
        self.assertEqual(findings, [])


class TestMathAndSummary(unittest.TestCase):
    """Taxable gallons, net tax, and summary math."""

    def test_fleet_mpg(self):
        rows, _findings, _total = parse(
            HEADER + row() + row("OK", "3000", "500", "500"))
        self.assertEqual(ftd.fleet_mpg(rows), Decimal("6"))

    def test_taxable_gallons(self):
        rates, _findings = load_rates(
            RATES_HEADER + "TX,0.20\n" + "OK,0.19\n")
        rows, findings = audit(
            HEADER + row() + row("OK", "3000", "500", "500"),
            rates=rates)
        self.assertEqual(findings, [])
        texas = [item for item in rows
                 if item["jurisdiction"] == "TX"][0]
        self.assertEqual(texas["taxable_gallons"], Decimal("1000.00"))
        self.assertEqual(texas["net_gallons"], Decimal("0.00"))
        self.assertEqual(texas["net_tax"], Decimal("0.00"))

    def test_net_tax_computed(self):
        rates, _findings = load_rates(RATES)
        rows, _findings = audit(
            HEADER + row(miles="3100", gallons="500",
                         tax_paid="200"),
            rates=rates)
        self.assertEqual(rows[0]["taxable_gallons"],
                         Decimal("500.00"))
        self.assertEqual(rows[0]["net_gallons"], Decimal("300.00"))
        self.assertEqual(rows[0]["net_tax"], Decimal("60.00"))

    def test_summary_totals(self):
        rates, _findings = load_rates(RATES)
        rows, findings = audit(
            HEADER + row() + row("OK", "3000", "500", "500"),
            rates=rates)
        summary = ftd.summarize(rows, findings, ftd.fleet_mpg(rows))
        self.assertEqual(summary["total_miles"], Decimal("9000"))
        self.assertEqual(summary["total_gallons"], Decimal("1500"))
        self.assertEqual(summary["total_taxable"], Decimal("1500"))
        self.assertEqual(summary["total_net_gallons"], Decimal("0"))
        self.assertEqual(summary["total_net_tax"], Decimal("0"))
        self.assertEqual(summary["fleet_mpg"], Decimal("6"))

    def test_summary_no_rates(self):
        rows, findings = audit(HEADER + row())
        summary = ftd.summarize(rows, findings, ftd.fleet_mpg(rows))
        self.assertIsNone(summary["total_net_tax"])
        self.assertIsNone(summary["total_credits"])

    def test_summary_groups_duplicates(self):
        rows, findings = audit(HEADER + row() + row(miles="900",
                                                     gallons="150",
                                                     tax_paid="150"))
        summary = ftd.summarize(rows, findings, ftd.fleet_mpg(rows))
        self.assertEqual(len(summary["per_jurisdiction"]), 1)
        self.assertEqual(summary["per_jurisdiction"][0]["miles"],
                         Decimal("6900"))

    def test_summary_counts(self):
        rows, findings = audit(HEADER + row(gallons="0",
                                            tax_paid="0"))
        summary = ftd.summarize(rows, findings, ftd.fleet_mpg(rows))
        self.assertEqual(summary["errors"], 2)
        self.assertEqual(summary["warnings"], 0)
        self.assertEqual(summary["findings"], 2)


class TestOutput(unittest.TestCase):
    """Text, JSON, and CLI behavior."""

    def test_render_text_clean(self):
        rows, findings = audit(HEADER + row())
        summary = ftd.summarize(rows, findings, ftd.fleet_mpg(rows))
        text = ftd.render_text(rows, findings, summary)
        self.assertIn("All jurisdictions clean.", text)
        self.assertIn("Fleet MPG: 6.00", text)

    def test_render_text_findings(self):
        rows, findings = audit(HEADER + row(gallons="0",
                                            tax_paid="0"))
        summary = ftd.summarize(rows, findings, ftd.fleet_mpg(rows))
        text = ftd.render_text(rows, findings, summary)
        self.assertIn("ERROR [miles-no-fuel]", text)
        self.assertIn("Jurisdictions: 1, findings: 2", text)

    def test_render_json(self):
        rows, findings = audit(HEADER + row())
        summary = ftd.summarize(rows, findings, ftd.fleet_mpg(rows))
        report = json.loads(ftd.render_json(rows, findings, summary))
        self.assertEqual(report["errors"], 0)
        self.assertEqual(report["total_miles"], "6000")
        self.assertEqual(report["fleet_mpg"], "6")
        self.assertIsNone(report["total_net_tax"])
        self.assertEqual(report["findings"], [])

    def test_render_json_findings(self):
        rows, findings = audit(HEADER + row(gallons="0",
                                            tax_paid="0"))
        summary = ftd.summarize(rows, findings, ftd.fleet_mpg(rows))
        report = json.loads(ftd.render_json(rows, findings, summary))
        self.assertEqual(len(report["findings"]), 2)
        self.assertEqual(
            sorted(item["rule"] for item in report["findings"]),
            ["implausible-mpg", "miles-no-fuel"])

    def test_main_check_exit_codes(self):
        path = write_file(HEADER + row(gallons="0", tax_paid="0"))
        self.assertEqual(ftd.main([path, "--as-of", "2026-11-18",
                                   "--check"]), 1)
        clean = write_file(HEADER + row())
        self.assertEqual(ftd.main([clean, "--as-of", "2026-11-18",
                                   "--check"]), 0)

    def test_main_strict_exit_codes(self):
        path = write_file(HEADER + row()
                          + row("SD", "600", "500", "480"))
        self.assertEqual(ftd.main([path, "--as-of", "2026-11-18",
                                   "--check"]), 0)
        self.assertEqual(ftd.main([path, "--as-of", "2026-11-18",
                                   "--check", "--strict"]), 1)

    def test_main_json_flag(self):
        path = write_file(HEADER + row())
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = ftd.main([path, "--as-of", "2026-11-18", "--json"])
        self.assertEqual(code, 0)
        report = json.loads(buffer.getvalue())
        self.assertEqual(report["jurisdictions"], 1)

    def test_main_out_file(self):
        path = write_file(HEADER + row())
        out = tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False).name
        code = ftd.main([path, "--as-of", "2026-11-18", "--out", out])
        self.assertEqual(code, 0)
        with open(out, encoding="utf-8") as handle:
            self.assertIn("All jurisdictions clean.", handle.read())

    def test_main_bad_as_of(self):
        path = write_file(HEADER + row())
        self.assertEqual(ftd.main([path, "--as-of", "not-a-date"]), 2)

    def test_main_min_above_max(self):
        path = write_file(HEADER + row())
        self.assertEqual(ftd.main([path, "--min-mpg", "9",
                                   "--max-mpg", "8"]), 2)

    def test_main_missing_rates_file(self):
        path = write_file(HEADER + row())
        self.assertEqual(ftd.main([path, "--rates-file",
                                   "/nonexistent/rates.csv"]), 2)

    def test_main_total_miles_flag(self):
        path = write_file(HEADER + row())
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = ftd.main([path, "--as-of", "2026-11-18",
                             "--total-miles", "6000"])
        self.assertEqual(code, 0)
        self.assertIn("findings: 0", buffer.getvalue())
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            ftd.main([path, "--as-of", "2026-11-18",
                      "--total-miles", "5000"])
        self.assertIn("miles-total-mismatch", buffer.getvalue())

    def test_sample_file(self):
        path = os.path.join(EXAMPLES, "fuel-tax.csv")
        rates = os.path.join(EXAMPLES, "rates.csv")
        rows, findings, total = ftd.parse_csv(path, AS_OF)
        rates_data, _rate_findings = ftd.load_rates(rates)
        findings.extend(ftd.audit(
            rows, AS_OF, rates=rates_data,
            declared_total_miles=total))
        summary = ftd.summarize(rows, findings, ftd.fleet_mpg(rows))
        self.assertEqual(summary["jurisdictions"], 19)
        self.assertEqual(summary["findings"], 25)
        self.assertEqual(summary["errors"], 14)
        self.assertEqual(summary["warnings"], 11)
        self.assertEqual(summary["total_miles"], Decimal("52900"))
        self.assertEqual(summary["total_gallons"], Decimal("8550"))
        self.assertEqual(summary["fleet_mpg"].quantize(
            Decimal("0.01")), Decimal("6.19"))
        self.assertEqual(summary["total_net_tax"], Decimal("101.95"))
        self.assertEqual(summary["total_credits"], Decimal("406.04"))

    def test_clean_file(self):
        path = os.path.join(EXAMPLES, "clean.csv")
        rows, findings, total = ftd.parse_csv(path, AS_OF)
        findings.extend(ftd.audit(
            rows, AS_OF, declared_total_miles=total))
        self.assertEqual(findings, [])

    def test_warnings_only_file(self):
        path = os.path.join(EXAMPLES, "warnings-only.csv")
        rows, findings, _total = ftd.parse_csv(path, AS_OF)
        findings.extend(ftd.audit(rows, AS_OF))
        self.assertEqual(len(findings), 2)
        self.assertEqual(
            sorted(item.severity for item in findings),
            ["warning", "warning"])
        self.assertEqual(ftd.main([path, "--as-of", "2026-11-18",
                                   "--check"]), 0)
        self.assertEqual(ftd.main([path, "--as-of", "2026-11-18",
                                   "--check", "--strict"]), 1)


if __name__ == "__main__":
    unittest.main()
