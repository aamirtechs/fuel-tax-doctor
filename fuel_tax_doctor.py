"""Quarterly IFTA fuel tax records audit CLI.

Reads a carrier's quarterly IFTA fuel tax records CSV
export (one row per jurisdiction: jurisdiction, miles,
gallons purchased, tax-paid gallons, quarter) and audits
it for the pre-filing checks that catch audit triggers:
fleet MPG outside the plausible band, jurisdictions with
miles but no fuel purchases, fuel purchases with no
miles, miles that do not sum to the quarterly total,
missing jurisdictions the fleet operated in, tax-paid
gallons exceeding purchased gallons, negative net gallons
that are not flagged as credits, and bad data such as
malformed rows, invalid numbers, and bad quarters. It
also computes taxable gallons and net tax per
jurisdiction at the rates in an optional rates file.

Every interstate carrier files an IFTA return every
quarter, with four fixed deadlines (April 30, July 31,
October 31, January 31). Late filing carries a penalty
of 50 dollars or 10 percent of net tax due, whichever is
greater, plus monthly interest, and poor records in an
audit can get a fleet's MPG reset to 4.00, with all
records kept four years. ELD and TMS platforms compute
IFTA inside paid SaaS and CPAs file it, but no free
offline CLI pre-checks the quarterly miles and gallons
file for the inconsistencies auditors look for before
the return goes in.

The math follows the IFTA method. Fleet MPG is total
miles divided by total gallons purchased across all
jurisdictions. Taxable gallons for a jurisdiction are
its miles divided by fleet MPG. Net taxable gallons are
taxable gallons minus tax-paid gallons in that
jurisdiction, and net tax is net taxable gallons times
the jurisdiction's tax rate. A negative net means the
carrier overpaid in that jurisdiction and is owed a
credit.

Each problem is a finding with a jurisdiction (or
"fleet" for fleet level findings, or "TOTAL" for the
quarter total), a rule name, a severity (error or
warning), and a message. Errors are audit triggers and
badly recorded numbers; warnings are patterns worth
investigating. --check exits 1 when any error is found,
and --strict also fails on warnings.

Gallons and money use Decimal math, so totals add up
exactly. A bad row is reported as a finding and the row
is still audited on the fields that did parse, so one
bad row never crashes the run.

Zero dependencies, Python 3.9 or newer, standard library only.

Usage:
    python3 fuel_tax_doctor.py examples/fuel-tax.csv --rates-file examples/rates.csv
    python3 fuel_tax_doctor.py fuel-tax.csv --json --check
    python3 fuel_tax_doctor.py fuel-tax.csv --as-of 2026-11-18 --total-miles 52900
"""

import argparse
import csv
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

__version__ = "1.0.0"

DEFAULT_MIN_MPG = Decimal("4")
DEFAULT_MAX_MPG = Decimal("8")
CENT = Decimal("0.01")
GALLON_STEP = Decimal("0.01")
MPG_STEP = Decimal("0.01")
TOLERANCE = Decimal("0.01")

QUARTER_ENDS = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
QUARTER_PATTERN = re.compile(
    r"^\s*(?:(\d{4})[- ]?Q([1-4])|Q([1-4])[- ]?(\d{4}))\s*$",
    re.IGNORECASE)


class Finding:
    """One audit finding for one jurisdiction, or fleet level."""

    def __init__(self, jurisdiction, rule, severity, message):
        self.jurisdiction = jurisdiction
        self.rule = rule
        self.severity = severity
        self.message = message

    def as_dict(self):
        """Return the finding as a plain dict."""
        return {"jurisdiction": self.jurisdiction, "rule": self.rule,
                "severity": self.severity, "message": self.message}

    def __str__(self):
        return "%s [%s] %s: %s" % (self.severity.upper(), self.rule,
                                   self.jurisdiction, self.message)


def parse_number(text):
    """Parse a number field, returning (value, is_valid, was_empty).

    Accepts a plain number with an optional dollar sign and
    thousands commas. An empty field means no number was
    recorded. A value that is not a number, or a negative
    number, is invalid.
    """
    if text is None:
        return None, True, True
    raw = text.strip().replace("$", "").replace(",", "")
    if not raw:
        return None, True, True
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None, False, False
    if value < 0:
        return None, False, False
    return value, True, False


def parse_quarter(text):
    """Parse a quarter string into (year, quarter), or None.

    Accepts 2026-Q3, 2026Q3, 2026 Q3, Q3 2026, and Q3-2026.
    Returns None for an empty string or a value that matches
    no known form.
    """
    if text is None:
        return None
    match = QUARTER_PATTERN.match(text)
    if match is None:
        return None
    if match.group(1) is not None:
        return int(match.group(1)), int(match.group(2))
    return int(match.group(4)), int(match.group(3))


def quarter_end(year, quarter):
    """Return the last day of a quarter as a date."""
    month, day = QUARTER_ENDS[quarter]
    return date(year, month, day)


def format_quarter(year, quarter):
    """Format a (year, quarter) pair as 2026-Q3."""
    return "%d-Q%d" % (year, quarter)


def find_column(header, names):
    """Return the first of names present in header, or None."""
    for name in names:
        if name in header:
            return name
    return None


def fmt_decimal(value, places=2):
    """Format a Decimal with two decimals, or n/a for None."""
    if value is None:
        return "n/a"
    return format(value.quantize(Decimal("0.01")), ",.2f")


def fmt_money(value):
    """Format a Decimal amount as dollars and cents, or n/a."""
    if value is None:
        return "n/a"
    return "$%s" % format(value.quantize(CENT), ",.2f")


def fmt_mpg(value):
    """Format a Decimal MPG with two decimals, or n/a for None."""
    if value is None:
        return "n/a"
    return format(value.quantize(MPG_STEP), ".2f")


def load_rates(path):
    """Load a tax rates CSV file into a dict of jurisdiction to rate.

    The file has a jurisdiction column and a rate column
    (dollars per gallon). Returns (rates, findings). A rate
    that is not a valid non-negative number is reported and
    skipped, never a crash.
    """
    findings = []
    rates = {}
    try:
        handle = open(path, newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError("cannot read %s: %s" % (path, exc))
    with handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError("%s is empty, no header row found" % path)
        except csv.Error as exc:
            raise ValueError("%s is not a readable CSV file: %s"
                             % (path, exc))
        header = [name.strip() for name in header]
        jurisdiction_column = find_column(
            header, ("jurisdiction", "state", "jurisdiction_code"))
        if jurisdiction_column is None:
            raise ValueError(
                "%s has no jurisdiction column (header is: %s)"
                % (path, ", ".join(header)))
        rate_column = find_column(
            header, ("rate", "tax_rate", "rate_per_gallon",
                     "tax_rate_per_gallon"))
        if rate_column is None:
            raise ValueError(
                "%s has no rate column (header is: %s)"
                % (path, ", ".join(header)))
        for line_number, row in enumerate(reader, start=2):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) != len(header):
                findings.append(Finding(
                    "row %d" % line_number, "malformed-row", "error",
                    "rates row has %d fields but the header has %d, "
                    "so the row was skipped" % (len(row), len(header))))
                continue
            cells = dict(zip(header, (cell.strip() for cell in row)))
            jurisdiction = cells[jurisdiction_column]
            if not jurisdiction:
                findings.append(Finding(
                    "row %d" % line_number, "empty-jurisdiction",
                    "error", "rates row has no jurisdiction, so "
                    "the row was skipped"))
                continue
            rate, valid, _empty = parse_number(cells[rate_column])
            if not valid:
                findings.append(Finding(
                    jurisdiction, "invalid-rate", "error",
                    "rate %r is not a valid non-negative number, "
                    "so the jurisdiction was left out of the rates"
                    % cells[rate_column]))
                continue
            if rate is None:
                findings.append(Finding(
                    jurisdiction, "invalid-rate", "error",
                    "rate is empty, so the jurisdiction was left "
                    "out of the rates"))
                continue
            rates[jurisdiction.upper()] = rate
    return rates, findings


def parse_csv(path, as_of, total_miles_arg=None):
    """Parse an IFTA fuel tax records CSV export.

    Returns (rows, findings, declared_total_miles). Each row
    is a dict with the jurisdiction, miles, gallons
    purchased, tax-paid gallons, quarter, credit flag, and
    the computed taxable gallons, net gallons, and net tax
    (filled in later by audit, once fleet MPG is known). A
    row whose jurisdiction is TOTAL declares the quarterly
    total miles and is not counted as a jurisdiction row.
    Rows with the wrong number of fields are reported as
    malformed-row errors and skipped, never a crash. A
    UTF-8 BOM header is handled. Extra columns are ignored.
    """
    findings = []
    rows = []
    declared_total_miles = total_miles_arg
    try:
        handle = open(path, newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError("cannot read %s: %s" % (path, exc))
    with handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError("%s is empty, no header row found" % path)
        except csv.Error as exc:
            raise ValueError("%s is not a readable CSV file: %s"
                             % (path, exc))
        header = [name.strip() for name in header]
        jurisdiction_column = find_column(
            header, ("jurisdiction", "state", "jurisdiction_code"))
        if jurisdiction_column is None:
            raise ValueError(
                "%s has no jurisdiction column (header is: %s)"
                % (path, ", ".join(header)))
        miles_column = find_column(
            header, ("miles", "total_miles", "jurisdiction_miles"))
        gallons_column = find_column(
            header, ("gallons_purchased", "gallons",
                     "fuel_purchased_gallons", "purchased_gallons"))
        tax_paid_column = find_column(
            header, ("tax_paid_gallons", "tax_paid",
                     "taxpaid_gallons", "gallons_tax_paid"))
        quarter_column = find_column(header, ("quarter", "period"))
        credit_column = find_column(header, ("credit", "is_credit",
                                             "credit_flag"))
        seen = set()
        for line_number, row in enumerate(reader, start=2):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) != len(header):
                findings.append(Finding(
                    "row %d" % line_number, "malformed-row", "error",
                    "row has %d fields but the header has %d, "
                    "so the row was skipped" % (len(row), len(header))))
                continue
            cells = dict(zip(header, (cell.strip() for cell in row)))
            jurisdiction = cells[jurisdiction_column]
            if not jurisdiction:
                findings.append(Finding(
                    "row %d" % line_number, "empty-jurisdiction",
                    "error", "row has no jurisdiction, so the row "
                    "was skipped"))
                continue
            if jurisdiction.upper() == "TOTAL":
                total, valid, _empty = parse_number(
                    cells[miles_column] if miles_column else "")
                if not valid:
                    findings.append(Finding(
                        "TOTAL", "invalid-amount", "error",
                        "TOTAL row miles %r is not a valid "
                        "non-negative number, so the quarterly "
                        "total could not be checked"
                        % cells[miles_column]))
                elif total is not None and declared_total_miles \
                        is None:
                    declared_total_miles = total
                continue
            key = (jurisdiction.upper(),
                   cells[quarter_column] if quarter_column else "")
            if key in seen:
                findings.append(Finding(
                    jurisdiction, "duplicate-jurisdiction", "error",
                    "jurisdiction appears more than once for the "
                    "same quarter, so its miles and gallons may "
                    "be counted twice"))
            else:
                seen.add(key)
            record = {"jurisdiction": jurisdiction,
                      "miles": None, "miles_empty": True,
                      "gallons": None, "gallons_empty": True,
                      "tax_paid": None, "tax_paid_empty": True,
                      "quarter": None, "quarter_raw": "",
                      "credit": False,
                      "taxable_gallons": None,
                      "net_gallons": None,
                      "rate": None,
                      "net_tax": None,
                      "line": line_number}
            if miles_column is not None:
                value, valid, empty = parse_number(cells[miles_column])
                record["miles_empty"] = empty
                if not valid:
                    findings.append(Finding(
                        jurisdiction, "invalid-amount", "error",
                        "miles %r is not a valid non-negative "
                        "number" % cells[miles_column]))
                else:
                    record["miles"] = value
            if gallons_column is not None:
                value, valid, empty = parse_number(
                    cells[gallons_column])
                record["gallons_empty"] = empty
                if not valid:
                    findings.append(Finding(
                        jurisdiction, "invalid-amount", "error",
                        "gallons_purchased %r is not a valid "
                        "non-negative number"
                        % cells[gallons_column]))
                else:
                    record["gallons"] = value
            if tax_paid_column is not None:
                value, valid, empty = parse_number(
                    cells[tax_paid_column])
                record["tax_paid_empty"] = empty
                if not valid:
                    findings.append(Finding(
                        jurisdiction, "invalid-amount", "error",
                        "tax_paid_gallons %r is not a valid "
                        "non-negative number"
                        % cells[tax_paid_column]))
                else:
                    record["tax_paid"] = value
            if quarter_column is not None:
                raw = cells[quarter_column]
                record["quarter_raw"] = raw
                if raw:
                    parsed = parse_quarter(raw)
                    if parsed is None:
                        findings.append(Finding(
                            jurisdiction, "invalid-quarter", "error",
                            "quarter %r is not a readable quarter "
                            "(use 2026-Q3)" % raw))
                    else:
                        record["quarter"] = parsed
                        end = quarter_end(parsed[0], parsed[1])
                        if end > as_of.date():
                            findings.append(Finding(
                                jurisdiction, "future-quarter",
                                "warning",
                                "quarter %s ends %s, after the "
                                "as-of date %s, so the quarter has "
                                "not ended yet"
                                % (format_quarter(parsed[0],
                                                 parsed[1]),
                                   end.isoformat(),
                                   as_of.strftime("%Y-%m-%d"))))
            if credit_column is not None:
                record["credit"] = cells[credit_column].strip().lower() \
                    in ("yes", "true", "1", "y")
            rows.append(record)
    return rows, findings, declared_total_miles


def fleet_mpg(rows):
    """Return the fleet MPG, or None when it cannot be computed.

    Fleet MPG is total miles divided by total gallons
    purchased. Rows with an invalid miles or gallons value
    are left out of both totals. When the fleet drove no
    miles or bought no fuel, MPG is undefined.
    """
    total_miles = sum((row["miles"] for row in rows
                       if row["miles"] is not None), Decimal("0"))
    total_gallons = sum((row["gallons"] for row in rows
                         if row["gallons"] is not None), Decimal("0"))
    if total_miles == 0 or total_gallons == 0:
        return None
    return total_miles / total_gallons


def audit(rows, as_of, min_mpg=DEFAULT_MIN_MPG,
          max_mpg=DEFAULT_MAX_MPG, rates=None,
          declared_total_miles=None):
    """Audit parsed rows and return a list of findings.

    Fleet MPG is computed first, then each jurisdiction's
    taxable gallons, net gallons, and net tax are filled
    in, then the per-jurisdiction rules run. The rates
    argument is a dict of jurisdiction to tax rate; when
    it is None, net tax is not computed and the rates
    checks are skipped.
    """
    findings = []
    mpg = fleet_mpg(rows)
    total_miles = sum((row["miles"] for row in rows
                       if row["miles"] is not None), Decimal("0"))
    if mpg is None:
        if total_miles > 0:
            findings.append(Finding(
                "fleet", "implausible-mpg", "error",
                "fleet MPG cannot be computed because total "
                "gallons purchased is zero while total miles "
                "is %s" % fmt_decimal(total_miles)))
    elif mpg < min_mpg or mpg > max_mpg:
        findings.append(Finding(
            "fleet", "implausible-mpg", "error",
            "fleet MPG is %s, outside the plausible band of "
            "%s to %s" % (fmt_mpg(mpg), fmt_mpg(min_mpg),
                          fmt_mpg(max_mpg))))
    if declared_total_miles is not None:
        difference = total_miles - declared_total_miles
        if abs(difference) > TOLERANCE:
            findings.append(Finding(
                "TOTAL", "miles-total-mismatch", "error",
                "jurisdiction miles sum to %s but the declared "
                "quarterly total is %s, a difference of %s miles"
                % (fmt_decimal(total_miles),
                   fmt_decimal(declared_total_miles),
                   fmt_decimal(abs(difference)))))
    quarters = [row["quarter"] for row in rows
                if row["quarter"] is not None]
    main_quarter = None
    if quarters:
        main_quarter = max(set(quarters), key=quarters.count)
    for row in rows:
        jurisdiction = row["jurisdiction"]
        miles = row["miles"]
        gallons = row["gallons"]
        tax_paid = row["tax_paid"]
        if row["quarter"] is not None \
                and main_quarter is not None \
                and row["quarter"] != main_quarter:
            findings.append(Finding(
                jurisdiction, "mixed-quarters", "error",
                "quarter %s does not match the rest of the "
                "file, which is %s"
                % (format_quarter(row["quarter"][0],
                                 row["quarter"][1]),
                   format_quarter(main_quarter[0],
                                 main_quarter[1]))))
        if miles is not None and miles > 0 \
                and (gallons == 0 or
                     (gallons is None and row["gallons_empty"])):
            findings.append(Finding(
                jurisdiction, "miles-no-fuel", "error",
                "drove %s miles but purchased no fuel in this "
                "jurisdiction" % fmt_decimal(miles)))
        if gallons is not None and gallons > 0 \
                and (miles == 0 or
                     (miles is None and row["miles_empty"])):
            findings.append(Finding(
                jurisdiction, "fuel-no-miles", "error",
                "purchased %s gallons but drove no miles in "
                "this jurisdiction" % fmt_decimal(gallons)))
        if tax_paid is not None and gallons is not None \
                and tax_paid > gallons:
            findings.append(Finding(
                jurisdiction, "tax-paid-exceeds-purchased", "error",
                "tax-paid gallons %s exceed purchased gallons "
                "%s" % (fmt_decimal(tax_paid),
                        fmt_decimal(gallons))))
        if mpg is not None and miles is not None:
            taxable = (miles / mpg).quantize(
                GALLON_STEP)
            row["taxable_gallons"] = taxable
            if tax_paid is not None:
                row["net_gallons"] = taxable - tax_paid
        if miles is not None and miles > 0 \
                and gallons is not None and gallons > 0:
            jurisdiction_mpg = miles / gallons
            if jurisdiction_mpg < min_mpg \
                    or jurisdiction_mpg > max_mpg:
                findings.append(Finding(
                    jurisdiction, "implausible-jurisdiction-mpg",
                    "warning",
                    "jurisdiction MPG is %s, outside the "
                    "plausible band of %s to %s"
                    % (fmt_mpg(jurisdiction_mpg),
                       fmt_mpg(min_mpg), fmt_mpg(max_mpg))))
        if row["net_gallons"] is not None and miles is not None \
                and miles > 0:
            if row["net_gallons"] < -TOLERANCE and not row["credit"]:
                findings.append(Finding(
                    jurisdiction, "negative-net-gallons", "warning",
                    "net taxable gallons are %s, a credit of %s "
                    "gallons that is not flagged as a credit"
                    % (fmt_decimal(row["net_gallons"]),
                       fmt_decimal(-row["net_gallons"]))))
            elif row["net_gallons"] >= -TOLERANCE and row["credit"]:
                findings.append(Finding(
                    jurisdiction, "credit-without-negative-net",
                    "warning",
                    "row is flagged as a credit but net taxable "
                    "gallons are %s, not negative"
                    % fmt_decimal(row["net_gallons"])))
        if rates is not None:
            rate = rates.get(jurisdiction.upper())
            if rate is None:
                findings.append(Finding(
                    jurisdiction, "missing-rate", "warning",
                    "no tax rate in the rates file, so net tax "
                    "could not be computed"))
            else:
                row["rate"] = rate
                if row["net_gallons"] is not None:
                    row["net_tax"] = (
                        row["net_gallons"] * rate).quantize(
                        CENT)
    if rates is not None:
        seen = set(row["jurisdiction"].upper() for row in rows)
        for jurisdiction in sorted(rates):
            if jurisdiction not in seen:
                findings.append(Finding(
                    jurisdiction, "missing-jurisdiction", "warning",
                    "jurisdiction is in the rates file but has "
                    "no rows in the records, so the fleet may "
                    "have operated there without reporting it"))
    return findings


def summarize(rows, findings, mpg):
    """Return the summary counts for rows and findings."""
    errors = sum(1 for item in findings if item.severity == "error")
    warnings = sum(1 for item in findings
                   if item.severity == "warning")
    total_miles = sum((row["miles"] for row in rows
                       if row["miles"] is not None), Decimal("0"))
    total_gallons = sum((row["gallons"] for row in rows
                         if row["gallons"] is not None), Decimal("0"))
    total_tax_paid = sum((row["tax_paid"] for row in rows
                          if row["tax_paid"] is not None),
                         Decimal("0"))
    total_taxable = sum((row["taxable_gallons"] for row in rows
                         if row["taxable_gallons"] is not None),
                        Decimal("0"))
    total_net = sum((row["net_gallons"] for row in rows
                     if row["net_gallons"] is not None), Decimal("0"))
    total_net_tax = sum((row["net_tax"] for row in rows
                         if row["net_tax"] is not None), Decimal("0"))
    total_credits = sum((-row["net_tax"] for row in rows
                         if row["net_tax"] is not None
                         and row["net_tax"] < 0), Decimal("0"))
    if not any(row["net_tax"] is not None for row in rows):
        total_net_tax = None
        total_credits = None
    per_jurisdiction = []
    grouped = {}
    for row in rows:
        key = row["jurisdiction"].upper()
        entry = grouped.setdefault(key, {
            "jurisdiction": row["jurisdiction"],
            "miles": Decimal("0"), "gallons": Decimal("0"),
            "tax_paid": Decimal("0"), "taxable": None,
            "net_gallons": None, "rate": row["rate"],
            "net_tax": None})
        if row["miles"] is not None:
            entry["miles"] += row["miles"]
        if row["gallons"] is not None:
            entry["gallons"] += row["gallons"]
        if row["tax_paid"] is not None:
            entry["tax_paid"] += row["tax_paid"]
        if row["taxable_gallons"] is not None:
            if entry["taxable"] is None:
                entry["taxable"] = Decimal("0")
            entry["taxable"] += row["taxable_gallons"]
        if row["net_gallons"] is not None:
            if entry["net_gallons"] is None:
                entry["net_gallons"] = Decimal("0")
            entry["net_gallons"] += row["net_gallons"]
        if row["net_tax"] is not None:
            if entry["net_tax"] is None:
                entry["net_tax"] = Decimal("0")
            entry["net_tax"] += row["net_tax"]
    for entry in sorted(grouped.values(),
                        key=lambda item: item["jurisdiction"]):
        per_jurisdiction.append(entry)
    return {"jurisdictions": len(rows),
            "findings": len(findings),
            "errors": errors,
            "warnings": warnings,
            "total_miles": total_miles,
            "total_gallons": total_gallons,
            "total_tax_paid": total_tax_paid,
            "total_taxable": total_taxable,
            "total_net_gallons": total_net,
            "fleet_mpg": mpg,
            "total_net_tax": total_net_tax,
            "total_credits": total_credits,
            "per_jurisdiction": per_jurisdiction}


def render_text(rows, findings, summary):
    """Render the text report."""
    lines = [str(item) for item in findings]
    lines.append("")
    lines.append("Jurisdictions: %(jurisdictions)d, findings: "
                 "%(findings)d, errors: %(errors)d, warnings: "
                 "%(warnings)d" % summary)
    lines.append("Total miles: %s, total gallons purchased: %s, "
                 "total tax-paid gallons: %s"
                 % (fmt_decimal(summary["total_miles"]),
                    fmt_decimal(summary["total_gallons"]),
                    fmt_decimal(summary["total_tax_paid"])))
    lines.append("Fleet MPG: %s, total taxable gallons: %s, "
                 "total net gallons: %s"
                 % (fmt_mpg(summary["fleet_mpg"]),
                    fmt_decimal(summary["total_taxable"]),
                    fmt_decimal(summary["total_net_gallons"])))
    lines.append("Total net tax: %s, total credits: %s"
                 % (fmt_money(summary["total_net_tax"]),
                    fmt_money(summary["total_credits"])))
    lines.append("")
    lines.append("Per-jurisdiction summary:")
    lines.append("%-13s %11s %11s %11s %11s %11s %7s %11s"
                 % ("jurisdiction", "miles", "gallons", "tax-paid",
                    "taxable", "net gal", "rate", "net tax"))
    for entry in summary["per_jurisdiction"]:
        lines.append("%-13s %11s %11s %11s %11s %11s %7s %11s"
                     % (entry["jurisdiction"],
                        fmt_decimal(entry["miles"]),
                        fmt_decimal(entry["gallons"]),
                        fmt_decimal(entry["tax_paid"]),
                        fmt_decimal(entry["taxable"]),
                        fmt_decimal(entry["net_gallons"]),
                        fmt_money(entry["rate"]) if entry["rate"]
                        is not None else "n/a",
                        fmt_money(entry["net_tax"])))
    if summary["findings"] == 0:
        lines.append("")
        lines.append("All jurisdictions clean.")
    return "\n".join(lines) + "\n"


def render_json(rows, findings, summary):
    """Render the JSON report."""
    report = dict(summary)
    for key in ("total_miles", "total_gallons", "total_tax_paid",
                "total_taxable", "total_net_gallons",
                "total_net_tax", "total_credits"):
        value = summary[key]
        report[key] = str(value) if value is not None else None
    report["fleet_mpg"] = (str(summary["fleet_mpg"])
                           if summary["fleet_mpg"] is not None
                           else None)
    for entry in report["per_jurisdiction"]:
        for key in ("miles", "gallons", "tax_paid", "taxable",
                    "net_gallons", "rate", "net_tax"):
            value = entry[key]
            entry[key] = str(value) if value is not None else None
    report["findings"] = [item.as_dict() for item in findings]
    return json.dumps(report, indent=2) + "\n"


def parse_decimal_arg(text):
    """Parse a Decimal CLI argument, or raise a usage error."""
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(
            "%r is not a valid number" % text)
    if value < 0:
        raise argparse.ArgumentTypeError(
            "value must be at least 0")
    return value


def parse_mpg_arg(text):
    """Parse an MPG CLI argument, or raise a usage error."""
    value = parse_decimal_arg(text)
    if value == 0:
        raise argparse.ArgumentTypeError(
            "MPG must be greater than 0")
    return value


def build_parser():
    """Build the command line parser."""
    parser = argparse.ArgumentParser(
        prog="fuel-tax-doctor",
        description="Audit a quarterly IFTA fuel tax records "
                    "CSV export for audit triggers before the "
                    "return goes in.")
    parser.add_argument("csv_file",
                        help="IFTA fuel tax records CSV to audit")
    parser.add_argument("--rates-file", default=None,
                        help="CSV of jurisdiction tax rates "
                        "(jurisdiction,rate) used to compute net "
                        "tax per jurisdiction")
    parser.add_argument("--total-miles", type=parse_decimal_arg,
                        default=None,
                        help="declared quarterly total miles, "
                        "checked against the sum of jurisdiction "
                        "miles (a TOTAL row in the CSV does the "
                        "same thing)")
    parser.add_argument("--min-mpg", type=parse_mpg_arg,
                        default=DEFAULT_MIN_MPG,
                        help="a fleet MPG below this is flagged "
                        "as implausible (default: 4)")
    parser.add_argument("--max-mpg", type=parse_mpg_arg,
                        default=DEFAULT_MAX_MPG,
                        help="a fleet MPG above this is flagged "
                        "as implausible (default: 8)")
    parser.add_argument("--as-of", default=None,
                        help="date to compare quarters against, "
                        "for example 2026-11-18 (default: now)")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of text")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if any error is found")
    parser.add_argument("--strict", action="store_true",
                        help="with --check, also exit 1 on warnings")
    parser.add_argument("--out", default=None,
                        help="write the report to a file")
    return parser


def parse_as_of(text):
    """Parse an --as-of date into an aware datetime, or None."""
    if text is None:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def main(argv=None):
    """Run the CLI and return the exit code."""
    args = build_parser().parse_args(argv)
    if args.min_mpg > args.max_mpg:
        print("error: --min-mpg must not be above --max-mpg",
              file=sys.stderr)
        return 2
    as_of = parse_as_of(args.as_of)
    if as_of is None:
        print("error: --as-of %r is not a readable date"
              % args.as_of, file=sys.stderr)
        return 2
    rates = None
    rate_findings = []
    if args.rates_file is not None:
        try:
            rates, rate_findings = load_rates(args.rates_file)
        except ValueError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
    try:
        rows, findings, declared_total = parse_csv(
            args.csv_file, as_of, total_miles_arg=args.total_miles)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    findings.extend(rate_findings)
    findings.extend(audit(rows, as_of,
                          min_mpg=args.min_mpg,
                          max_mpg=args.max_mpg,
                          rates=rates,
                          declared_total_miles=declared_total))
    summary = summarize(rows, findings, fleet_mpg(rows))
    if args.json:
        report = render_json(rows, findings, summary)
    else:
        report = render_text(rows, findings, summary)
    if args.out is not None:
        try:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(report)
        except OSError as exc:
            print("error: cannot write %s: %s" % (args.out, exc),
                  file=sys.stderr)
            return 2
    else:
        sys.stdout.write(report)
    if args.check:
        if summary["errors"] > 0:
            return 1
        if args.strict and summary["warnings"] > 0:
            return 1
    return 0


def main_entry():
    """Console script entry point."""
    sys.exit(main())


if __name__ == "__main__":
    main_entry()
