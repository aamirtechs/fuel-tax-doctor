# Changelog

## 1.0.0 - 2026-11-25

Initial release.

* Quarterly IFTA fuel tax records audit over a carrier's
  records CSV export: fleet MPG outside the plausible
  band, jurisdictions with miles but no fuel purchases,
  fuel purchases with no miles, miles that do not sum to
  the quarterly total, missing jurisdictions the fleet
  operated in, tax-paid gallons exceeding purchased
  gallons, and negative net gallons that are not flagged
  as credits.
* IFTA math with Decimal precision: fleet MPG is total
  miles over total gallons purchased, taxable gallons
  are jurisdiction miles over fleet MPG, net taxable
  gallons are taxable gallons minus tax-paid gallons,
  and net tax is net gallons times the jurisdiction rate
  from an optional rates file, plus a per-jurisdiction
  summary table.
* Data quality checks: duplicate jurisdictions for one
  quarter, malformed rows, empty jurisdictions, invalid
  or negative numbers, invalid quarters, mixed quarters
  in one file, and quarters that end after the as-of
  date.
* A TOTAL row in the CSV, or --total-miles on the
  command line (which wins), declares the quarterly
  total miles for the miles-sum check; a credit column
  marks rows already flagged as credits.
* Defensive quarter parsing (2026-Q3, 2026Q3, 2026 Q3,
  Q3 2026, Q3-2026); a bad row is reported as a finding
  and never crashes the run. UTF-8 BOM headers and extra
  columns handled.
* Findings carry a jurisdiction (or fleet, or TOTAL), a
  rule name, a severity (error or warning), and a
  message; errors and warnings are counted in the
  summary.
* Text and --json output, --out file output, --rates-file,
  --total-miles, --min-mpg, --max-mpg, and --as-of
  thresholds, and a --check CI gate that exits 1 on
  errors (and on warnings with --strict).
* Zero dependencies, Python 3.9 or newer.
