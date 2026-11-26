# fuel-tax-doctor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/aamirtechs/fuel-tax-doctor/blob/main/LICENSE) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/aamirtechs/fuel-tax-doctor/issues)

A zero-dependency Python CLI that audits a carrier's
quarterly IFTA fuel tax records CSV export for the
pre-filing checks that catch audit triggers: fleet MPG
outside the plausible band, jurisdictions with miles but
no fuel purchases, fuel purchases with no miles, miles
that do not sum to the quarterly total, missing
jurisdictions the fleet operated in, tax-paid gallons
exceeding purchased gallons, negative net gallons that
are not flagged as credits, and bad data such as
malformed rows, invalid numbers, and bad quarters. It
also computes taxable gallons and net tax per
jurisdiction at the rates in an optional rates file, so
a dispatcher can see what the return should say before
it goes in.

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

## Install

Requires Python 3.9 or newer. No dependencies beyond the
standard library.

```bash
pip install fuel-tax-doctor
```

Or run it straight from the source checkout:

```bash
python3 fuel_tax_doctor.py examples/fuel-tax.csv --rates-file examples/rates.csv
```

## Input format

A CSV export with a header row, one row per jurisdiction.
Columns used: `jurisdiction` (or `state`,
`jurisdiction_code`), `miles`, `gallons_purchased`,
`tax_paid_gallons`, `quarter`, and an optional `credit`
flag (`yes`, `true`, `1`, or `y`). Extra columns are
ignored, so a full IFTA records export works as-is.
A UTF-8 BOM header is handled. Only `jurisdiction` is
required; the other columns are optional, and the checks
that need a missing column are skipped.

`miles` also accepts `total_miles` or
`jurisdiction_miles`; `gallons_purchased` also accepts
`gallons`, `fuel_purchased_gallons`, or
`purchased_gallons`; `tax_paid_gallons` also accepts
`tax_paid`, `taxpaid_gallons`, or `gallons_tax_paid`;
`quarter` also accepts `period`.

A row whose jurisdiction is `TOTAL` declares the
quarterly total miles and is not counted as a
jurisdiction row; `--total-miles` does the same thing
from the command line and wins over a TOTAL row.
Quarters accept `2026-Q3`, `2026Q3`, `2026 Q3`,
`Q3 2026`, and `Q3-2026`. Numbers accept a plain number
with an optional dollar sign and thousands commas.

```csv
jurisdiction,miles,gallons_purchased,tax_paid_gallons,quarter,credit
TX,12500,1800,1700,2026-Q3,
OK,8200,1200,1300,2026-Q3,
NM,5400,0,0,2026-Q3,
LA,3100,500,200,2026-Q3,
TOTAL,47000,,,,
```

The rates file is a CSV with a `jurisdiction` column and
a `rate` column (dollars per gallon):

```csv
jurisdiction,rate
TX,0.20
OK,0.19
NM,0.21
WY,0.24
```

The math follows the IFTA method. Fleet MPG is total
miles divided by total gallons purchased across all
jurisdictions. Taxable gallons for a jurisdiction are
its miles divided by fleet MPG. Net taxable gallons are
taxable gallons minus tax-paid gallons in that
jurisdiction, and net tax is net taxable gallons times
the jurisdiction's tax rate. A negative net means the
carrier overpaid in that jurisdiction and is owed a
credit. Without a rates file, net tax is not computed
and shows as `n/a`.

## What it checks

Errors, the ones that mean an audit trigger or badly
recorded numbers:

* `implausible-mpg`: fleet MPG outside `--min-mpg` to
  `--max-mpg` (default 4 to 8), or fleet MPG that cannot
  be computed because the fleet drove miles but bought
  no fuel.
* `miles-no-fuel`: the jurisdiction drove miles but
  purchased no fuel there.
* `fuel-no-miles`: the jurisdiction purchased fuel but
  drove no miles there.
* `tax-paid-exceeds-purchased`: tax-paid gallons are
  above purchased gallons in the jurisdiction.
* `miles-total-mismatch`: jurisdiction miles do not sum
  to the declared quarterly total (TOTAL row or
  `--total-miles`), with the difference.
* `mixed-quarters`: a row whose quarter does not match
  the rest of the file.
* `duplicate-jurisdiction`: the same jurisdiction twice
  for the same quarter, so its miles and gallons may be
  counted twice.
* `malformed-row`: a row with the wrong number of
  fields. The row is skipped and reported, never a
  crash.
* `empty-jurisdiction`: a row with no jurisdiction. The
  row is skipped and reported.
* `invalid-amount`: miles, gallons, or tax-paid gallons
  that are not valid non-negative numbers.
* `invalid-quarter`: a quarter that matches no known
  form (use `2026-Q3`).

Warnings, patterns worth investigating:

* `negative-net-gallons`: net taxable gallons are
  negative, a credit that is not flagged as a credit
  in the `credit` column.
* `credit-without-negative-net`: a row flagged as a
  credit whose net taxable gallons are not negative.
* `implausible-jurisdiction-mpg`: one jurisdiction's
  MPG (its miles over its gallons) outside the
  plausible band.
* `missing-jurisdiction`: a jurisdiction in the rates
  file with no rows in the records, so the fleet may
  have operated there without reporting it.
* `missing-rate`: a jurisdiction with no rate in the
  rates file, so its net tax could not be computed.
* `future-quarter`: a quarter that ends after `--as-of`
  (default now), so the quarter has not ended yet.

The summary reports total miles, total gallons
purchased, total tax-paid gallons, fleet MPG, total
taxable gallons, total net gallons, total net tax, and
total credits, with a per-jurisdiction table of miles,
gallons, tax-paid gallons, taxable gallons, net gallons,
rate, and net tax. Duplicate jurisdiction rows are
grouped into one table row.

## Usage

```bash
python3 fuel_tax_doctor.py examples/fuel-tax.csv --rates-file examples/rates.csv --as-of 2026-11-18
```

Captured output:

```
ERROR [duplicate-jurisdiction] TX: jurisdiction appears more than once for the same quarter, so its miles and gallons may be counted twice
ERROR [invalid-amount] KS: miles 'abc' is not a valid non-negative number
ERROR [invalid-amount] MO: gallons_purchased '-50' is not a valid non-negative number
WARNING [future-quarter] IA: quarter 2026-Q4 ends 2026-12-31, after the as-of date 2026-11-18, so the quarter has not ended yet
ERROR [malformed-row] row 17: row has 3 fields but the header has 6, so the row was skipped
ERROR [empty-jurisdiction] row 20: row has no jurisdiction, so the row was skipped
ERROR [invalid-quarter] KY: quarter 'QX' is not a readable quarter (use 2026-Q3)
ERROR [miles-total-mismatch] TOTAL: jurisdiction miles sum to 52,900.00 but the declared quarterly total is 47,000.00, a difference of 5,900.00 miles
ERROR [tax-paid-exceeds-purchased] OK: tax-paid gallons 1,300.00 exceed purchased gallons 1,200.00
ERROR [miles-no-fuel] NM: drove 5,400.00 miles but purchased no fuel in this jurisdiction
ERROR [fuel-no-miles] AR: purchased 450.00 gallons but drove no miles in this jurisdiction
ERROR [tax-paid-exceeds-purchased] MS: tax-paid gallons 600.00 exceed purchased gallons 300.00
WARNING [negative-net-gallons] MS: net taxable gallons are -260.59, a credit of 260.59 gallons that is not flagged as a credit
WARNING [negative-net-gallons] AL: net taxable gallons are -24.20, a credit of 24.20 gallons that is not flagged as a credit
ERROR [tax-paid-exceeds-purchased] GA: tax-paid gallons 900.00 exceed purchased gallons 700.00
WARNING [negative-net-gallons] GA: net taxable gallons are -269.66, a credit of 269.66 gallons that is not flagged as a credit
WARNING [negative-net-gallons] TX: net taxable gallons are -4.54, a credit of 4.54 gallons that is not flagged as a credit
ERROR [mixed-quarters] NE: quarter 2026-Q2 does not match the rest of the file, which is 2026-Q3
WARNING [negative-net-gallons] NE: net taxable gallons are -6.05, a credit of 6.05 gallons that is not flagged as a credit
ERROR [mixed-quarters] IA: quarter 2026-Q4 does not match the rest of the file, which is 2026-Q3
WARNING [negative-net-gallons] IA: net taxable gallons are -18.37, a credit of 18.37 gallons that is not flagged as a credit
WARNING [implausible-jurisdiction-mpg] SD: jurisdiction MPG is 1.20, outside the plausible band of 4.00 to 8.00
WARNING [negative-net-gallons] SD: net taxable gallons are -383.02, a credit of 383.02 gallons that is not flagged as a credit
WARNING [implausible-jurisdiction-mpg] ND: jurisdiction MPG is 1.25, outside the plausible band of 4.00 to 8.00
WARNING [missing-jurisdiction] WY: jurisdiction is in the rates file but has no rows in the records, so the fleet may have operated there without reporting it

Jurisdictions: 19, findings: 25, errors: 14, warnings: 11
Total miles: 52,900.00, total gallons purchased: 8,550.00, total tax-paid gallons: 8,260.00
Fleet MPG: 6.19, total taxable gallons: 8,550.00, total net gallons: 590.00
Total net tax: $101.95, total credits: $406.04

Per-jurisdiction summary:
jurisdiction        miles     gallons    tax-paid     taxable     net gal    rate     net tax
AL               4,800.00      800.00      800.00      775.80      -24.20   $0.19      $-4.60
AR                   0.00      450.00      450.00        0.00     -450.00   $0.22     $-99.00
CO                 800.00      100.00        0.00      129.30      129.30   $0.22      $28.45
FL               2,600.00      420.00      100.00      420.23      320.23   $0.34     $108.88
GA               3,900.00      700.00      900.00      630.34     -269.66   $0.29     $-78.20
IA               1,000.00      180.00      180.00      161.63      -18.37   $0.30      $-5.51
KS                   0.00      300.00      300.00         n/a         n/a   $0.26         n/a
KY               2,000.00      300.00      300.00      323.25       23.25   $0.26       $6.04
LA               3,100.00      500.00      200.00      501.04      301.04   $0.20      $60.21
MO               1,500.00        0.00        0.00      242.44      242.44   $0.17      $41.21
MS               2,100.00      300.00      600.00      339.41     -260.59   $0.18     $-46.91
ND                 500.00      400.00      350.00       80.81     -269.19   $0.23     $-61.91
NE               1,200.00      200.00      200.00      193.95       -6.05   $0.29      $-1.75
NM               5,400.00        0.00        0.00      872.78      872.78   $0.21     $183.28
OK               8,200.00    1,200.00    1,300.00    1,325.33       25.33   $0.19       $4.81
SD                 600.00      500.00      480.00       96.98     -383.02   $0.28    $-107.25
TN               1,800.00      250.00      250.00      290.93       40.93   $0.27      $11.05
TX              13,400.00    1,950.00    1,850.00    2,165.78      315.78   $0.20      $63.15
```

The sample has 22 rows; the malformed row 17 and the
row with no jurisdiction are skipped, so 19
jurisdictions are audited. The headline numbers are
52,900.00 total miles, 8,550.00 gallons purchased,
fleet MPG 6.19, total net tax $101.95, and total credits
$406.04. The worst cases are SD, with 383.02 gallons of
unflagged credit and a 1.20 MPG, OK, MS, and GA, where
tax-paid gallons exceed purchased gallons, NM, with
5,400.00 miles and no fuel, and the quarterly total,
which is 5,900.00 miles short of the jurisdiction sum.
ND has a negative net too, but its `credit` flag is set,
so no warning fires for it. CO sits exactly on the 8.00
MPG boundary and passes.

Use it as a CI gate: `--check` exits 1 when any error is
found, and 0 otherwise. On the sample above it exits 1:

```bash
python3 fuel_tax_doctor.py examples/fuel-tax.csv --rates-file examples/rates.csv --as-of 2026-11-18 --check
```

On `examples/clean.csv`, three clean jurisdictions, it
exits 0:

```
Jurisdictions: 3, findings: 0, errors: 0, warnings: 0
Total miles: 10,200.00, total gallons purchased: 1,700.00, total tax-paid gallons: 1,700.00
Fleet MPG: 6.00, total taxable gallons: 1,700.00, total net gallons: 0.00
Total net tax: n/a, total credits: n/a

Per-jurisdiction summary:
jurisdiction        miles     gallons    tax-paid     taxable     net gal    rate     net tax
NM               1,200.00      200.00      200.00      200.00        0.00     n/a         n/a
OK               3,000.00      500.00      500.00      500.00        0.00     n/a         n/a
TX               6,000.00    1,000.00    1,000.00    1,000.00        0.00     n/a         n/a

All jurisdictions clean.
```

With `--strict`, `--check` also fails on warnings.
`examples/warnings-only.csv` has one jurisdiction with
an unflagged credit and one at 12.00 MPG, so it passes
`--check` (exit 0) and fails `--check --strict`
(exit 1). With the correct quarterly total the mismatch
drops out of the sample report:

```bash
python3 fuel_tax_doctor.py examples/fuel-tax.csv --as-of 2026-11-18 --total-miles 52900 --check
```

That run still exits 1 on the sample's errors, but the
`miles-total-mismatch` error is gone, leaving 23
findings and 13 errors. With a tighter MPG band the
fleet itself is flagged:

```bash
python3 fuel_tax_doctor.py examples/fuel-tax.csv --rates-file examples/rates.csv --as-of 2026-11-18 --min-mpg 7 --check
```

That run adds the `implausible-mpg` error for the fleet
at 6.19 MPG, leaving 36 findings, 15 errors, and 21
warnings.

`--json` emits the summary, the per-jurisdiction table,
and the findings as JSON (money and gallons as strings,
to keep the decimals exact), and `--out` writes the
report to a file.

## Options

| Flag | Meaning |
| --- | --- |
| `--rates-file` | CSV of jurisdiction tax rates (jurisdiction,rate) used to compute net tax per jurisdiction |
| `--total-miles` | Declared quarterly total miles, checked against the sum of jurisdiction miles (a TOTAL row in the CSV does the same thing; the flag wins) |
| `--min-mpg` | A fleet MPG below this is flagged as implausible (default: 4) |
| `--max-mpg` | A fleet MPG above this is flagged as implausible (default: 8) |
| `--as-of` | Date to compare quarters against, for example 2026-11-18 (default: now) |
| `--json` | Emit JSON instead of text |
| `--check` | Exit 1 if any error is found |
| `--strict` | With --check, also exit 1 on warnings |
| `--out` | Write the report to a file |

## Edge cases

* A row with too many or too few fields is a malformed-row
  error and is skipped; the rest of the file is still
  audited.
* A row with no jurisdiction is an empty-jurisdiction
  error and is skipped.
* A bad quarter is an invalid-quarter error, and the row
  is still audited on the fields that did parse, so one
  bad value never hides the other problems on the row.
* A row with invalid miles is left out of taxable gallons
  and net gallons, so the net total only covers
  jurisdictions where taxable gallons could be computed.
  In the sample, KS has 300.00 tax-paid gallons but no
  taxable gallons, which is why total net gallons
  (590.00) is not simply total taxable gallons minus
  total tax-paid gallons.
* A jurisdiction with zero miles gets zero taxable
  gallons and a negative net equal to its tax-paid
  gallons, which is a credit, but no negative-net
  warning fires for it because the fuel-no-miles error
  already covers it.
* A fleet that drove no miles, or bought no fuel, has no
  defined MPG, so taxable gallons are not computed and no
  implausible-mpg error fires for the zero case alone.
* A one hundredth of a gallon difference is ignored as
  rounding, and a jurisdiction MPG exactly on the band
  edge passes, so normal rounding does not flag a row.
* A blank line in the file is skipped silently. A
  jurisdiction that appears twice is grouped into one
  row in the per-jurisdiction table.
* `--as-of` makes runs reproducible: without it, the
  future-quarter check compares against the current time.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

81 tests, all passing: quarter and number parsing,
header aliases and BOM handling, TOTAL rows, duplicate
jurisdictions, malformed rows, empty jurisdictions,
invalid amounts and quarters, mixed quarters, future
quarters, miles-total mismatch, miles-no-fuel,
fuel-no-miles, tax-paid exceeding purchased, fleet and
jurisdiction MPG bands and boundaries, negative net
gallons and credit flags, missing jurisdictions and
rates, rates file errors, taxable gallons and net tax
math, summary counts and money totals, per-jurisdiction
tables, text and JSON output, and `--check` and
`--strict` exit codes. The same 81 tests pass under
pytest.

## License

MIT. See [LICENSE](LICENSE).
