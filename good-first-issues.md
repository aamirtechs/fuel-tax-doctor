# Good first issues

Seeded issues for new contributors. File each one on GitHub with
the `good first issue` and `help wanted` labels.

## 1. Add a largest credits table

Title: Show the jurisdictions owed the largest credits

Description: After the per-jurisdiction summary, print a short
table of the five jurisdictions with the largest credits
(jurisdiction, net gallons, net tax), so a carrier can see at
a glance which credits to claim first. Only jurisdictions
with a negative net tax appear. Ties should break by
jurisdiction code.

Difficulty: Easy

Acceptance criteria: the table appears in text output with the
correct top five for the sample file (SD at $-107.25 first,
then AR at $-99.00, GA at $-78.20, ND at $-61.91, and MS at
$-46.91); JSON output is unchanged; a test checks the table
rows.

## 2. Report the largest net tax owed in the summary

Title: Print the jurisdiction with the largest net tax owed

Description: Add one line to the text summary naming the
jurisdiction with the largest net tax owed (jurisdiction,
taxable gallons, net gallons, net tax), so a carrier can act
on the worst case without reading every finding. When no
jurisdiction owes net tax, the line is omitted.

Difficulty: Easy

Acceptance criteria: the line appears for the sample file
naming NM, 872.78 taxable gallons, 872.78 net gallons,
$183.28; it is absent for the clean file; JSON output is
unchanged; a test checks both cases.

## 3. Let teams set a credit threshold

Title: Only flag unflagged credits above a size

Description: The negative-net-gallons rule flags every
unflagged credit, however small. Add a --credit-threshold
flag (gallons) so only credits above that size are flagged.
When the flag is absent, every unflagged credit is flagged
as today.

Difficulty: Easy

Acceptance criteria: with --credit-threshold 100 the
negative-net-gallons warnings for AL (-24.20 gallons), NE
(-6.05 gallons), IA (-18.37 gallons), and the second TX row
(-4.54 gallons) disappear from the sample report, leaving
the warnings for MS, GA, and SD, so the sample drops from
25 findings and 11 warnings to 21 findings and 7 warnings;
a test covers the threshold; README documents the flag.

## 4. Export the per-jurisdiction table as CSV

Title: Write the per-jurisdiction summary to a CSV file

Description: Add a --table-out flag that writes the
per-jurisdiction summary table (jurisdiction, miles,
gallons, tax_paid, taxable, net_gallons, rate, net_tax) to
a CSV file, so the numbers can go straight into a
spreadsheet or the IFTA return worksheet. The text report
is unchanged.

Difficulty: Easy

Acceptance criteria: with --table-out table.csv the file
has the header above and 18 rows for the sample file
(grouped jurisdictions), and the TX row reads 13400.00,
1950.00, 1850.00, 2165.78, 315.78, 0.20, 63.15; a test
checks the file contents.
