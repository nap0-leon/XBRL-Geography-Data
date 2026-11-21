# Validation

Three checks, in order of strength.

## 1. Against Compustat, with held-out years

Compustat first-reported geographic segments (WRDS, values as of the original filing) are available from fiscal
2020. The rules were tuned on fiscal 2020-2022 and frozen on 27 October 2026. Fiscal 2023-2026 were computed but not
looked at until then.

A firm-year is

- **exact**: the domestic row agrees within 2% (or is absent on both sides), and every foreign country either side
  names is named by the other within 2%;
- **equivalent**: not exact, but domestic and total agree within 2%, so the same numbers are cut at different
  resolutions;
- **other cut** or **countries agree**: the two cuts nest into each other, or every country agrees and the regions do
  not;
- **relabelled**, **partial**, **partial unchecked**, **total only**: the numbers are consistent but cannot be fully
  matched (for example XBRL tags less than 98% of revenue);
- **differ**: none of the above.

| years | firm-years | exact | equivalent | other cut | countries agree | relabelled | partial | partial unchecked | total only | differ | agree |
|:--|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| tuning, 2020-2022 | 4,701 | 83.6% | 4.4% | 1.7% | 0.3% | 1.9% | 2.0% | 2.4% | 2.8% | 0.9% | 90.0% |
| held out, 2023-2026 | 4,706 | 85.4% | 3.7% | 1.5% | 0.3% | 1.6% | 2.0% | 1.9% | 2.7% | 0.8% | 90.9% |

The table scores the released data: the classification rules as frozen on 27 October 2026, with the reference totals of
`code/build/03zbb_fsn_geography_reference.py` and the 93 row corrections listed under "Known limits". Those two changes came
after the freeze and moved agreement up by 0.1 points in the tuning years and 0.2 in the held-out years (19 firm-years
changed category: 16 to a better one and 3 to a worse one, where a reference that now follows the filing's own figure
exposed a mismatch with Compustat's record of the firm). The row
corrections were found by reading filings. The reference rule was chosen with Compustat sales as a yardstick on the tuning
years (see "Known limits"), so the held-out column checks it. "Agree" sums exact, equivalent, other cut and countries agree.
Agreement is scored at the resolution both sides report, so a firm-year where one side names countries and the other a
region that holds them still agrees when the domestic and total revenue agree. Where either side names a foreign country
(4,437 firm-years), both name 8,719 of Compustat's 9,616 and of this dataset's 9,756 country rows.

## 2. Against the 10-K, where the two disagree

Compustat is not ground truth. For 150 sampled disagreements from the tuning years, a script fetched the 10-K and
checked whether each side's numbers are printed in it. A calibration sample of 40 exact firm-years measures how often
the search misses a number that is there.

| sample | firm-years | this dataset's rows all printed | Compustat's rows all printed |
|:--|--:|--:|--:|
| calibration (exact) | 40 | 88% | 85% |
| disagreements | 150 | 75% | 64% |

Correcting for the search's hit rate, this dataset reproduces the filing in about 98% of firm-years.

## 3. Years Compustat cannot check (fiscal 2011-2019)

A standard Compustat subscription has no first-reported geographic segments before fiscal 2020. The same 10-K check
was run on 179 filings from fiscal 2011-2019 (about 20 per year) and on 60 from fiscal 2020-2022 as a benchmark.

| years | filings | all rows printed | share of rows printed |
|:--|--:|--:|--:|
| 2011-2014 | 79 | 91% | 96% |
| 2015-2019 | 100 | 82% | 87% |
| 2011-2019 | 179 | 86% | 91% |
| 2020-2022 benchmark | 60 | 88% | 90% |

The difference between 2011-2019 and the benchmark is -2.3 points with a standard error of 4.9 points. The untested
years reproduce the filings about as often as the validated ones. 2015-2019 reads somewhat lower than 2011-2014;
with 20 filings per year, that gap is within sampling noise, but treat those years with the usual care.

## Internal consistency

Share of filings whose used rows sum to the filing's own total revenue within 1%: 88% to 90% in 2011-2019, 90% to
92% in 2020-2025 (see `data/COVERAGE.md`). A filing that does not sum keeps all its rows; check `coverage` in
`filing_summary` before treating its rows as a complete split.

## Known limits

- A member name no rule can read (`Other`, `GroupOne`) stays a region with an unknown set of countries.
- `DomesticCountry` and a few similar names are classified as regions, not as the domestic row. The rules were frozen
  before this was found, so it is listed here and not fixed in this release.
- **The reference total.** `filing_revenue_musd` is the filing's own annual revenue fact in SEC companyfacts, found by
  accession number (`code/build/03zbb_fsn_geography_reference.py`). A filing that tags revenue under several tags keeps the
  value `sec_firm_year` chose, unless that is under half the largest value of the filing; of two values 1,000 times apart,
  which is a filer's scale error, one is dropped. 55 filings to 2025 have no such fact, and the step that sets aside rows
  summing above the total is skipped for them. The total can still be a different fact from the one a reader expects: EACO
  2012 reads 113.4 million (`SalesRevenueNet`) and the 10-K's segment note prints 114.6 million, so `coverage` is 1.011. The
  rule was chosen on the tuning years: on filings that link to Compustat, the reference is within 5% of Compustat sales for
  98.5% in the tuning years and 98.2% in the held-out years (Compustat sales net out items such as excise tax, so this is a
  yardstick, not proof). The threshold of one half is a round number picked after a look at 0.3 to 0.9 on all years, so the
  held-out figure checks the rule and is not a clean test of the threshold. An earlier build took the total from another
  filing of the same CIK and year when a filing had none of its own; that returned an 8-K with recast figures (Visteon
  2014), the prior year (Karbon-X 2025, Mantra Venture 2016) and a different line (EACO 2012), and it made the nesting step
  miss region totals (Visteon's Asia and Europe counted twice). There is no such fallback now.
- Fiscal 2026 is a stub (84 filings).
- **Negative values.** 310 used rows carry negative revenue (297 `region`, 7 `country`, 5 `domestic`, 1 `foreign_total`;
  minimum -2,009 million). 232 have a member containing 'Elimination' (for example `IntersegmentElimination`,
  `GeographyEliminations`); the rest are unallocated, corporate or reconciling items, or the country rows of the next
  bullet. They stay in the table because they make the used rows sum to the filing total. Drop rows whose member contains
  'Elimination' before you compute country or regional shares. A later release will add `kind = elimination`.
  `code/tests/check_negative_rows.py` lists them.
- **93 corrected rows (after the rules froze; `data/corrections.csv` has the old and new value of each).** The 25 negative
  `country` and `domestic` rows were checked against the 10-K (`code/tests/check_negative_printed.py`, then by reading the
  text where a number matched unrelated text). The 10-K prints the negative value in 11 of them: SAExploration (Peru,
  2017), Leet Technology (Bangladesh, 2024), Virtu (United Kingdom, 2019), StepStone (US, fiscal 2022), Frequency
  Electronics (US, 2023 to 2025, an intersegment line), SPX (South Africa, 2019), FibroGen (Japan, 2024), Freedom Holding
  (Russia, 2021) and 4D Molecular (US, 2020). Presbia (Netherlands, 2016, 0.001 million) sums to the filing total and
  stays. `code/build/03zda_release_corrections.py` corrects the other 13, and further rows with the same causes, without
  changing any rule:
  - 8 values had the wrong sign: L-3 fiscal 2010 (7 rows, the 10-K prints positive, including the US value of 13,747
    million) and Cytosorbents (Germany, 2017). The sign was flipped.
  - 12 rows in three Chembio filings (fiscal 2013, 2015 and 2016) hold year-on-year changes in sales that the filer tagged
    on the geography axis, not revenue levels: Mexico 2015; Brazil, Nigeria, US, Uganda, Mexico, a European decrease and
    two Asia increases tagged `MiddleEast` in 2016; Uganda, Ethiopia and a Middle East decrease tagged 483,000 million in
    2013. Each is quoted in the filing's discussion of sales. They keep their value and carry `nested = change_not_level`.
  - 72 rows in 19 filings had a scale error: the filer tagged thousands as dollars or the reverse, so the used rows summed
    to exactly 1,000 or 0.001 times the filing's own total revenue (`coverage` of 1000 or 0.001). Every row of each filing
    was multiplied or divided by 1,000, taking the total as the right scale. Rows 1,000 times too small: Simulations Plus
    (2012, 2013), F5 Networks (2011), Summit Wireless (2019), Kopin (2016), CHS (2014 to 2016), Female Health (2013), Socket
    Mobile (2018, 2019). Rows 1,000 times too large: Quantum Fuel (2014), NanoVibronix (2023), SS Innovations (2023), Tigo
    Energy (2023 to 2025), Solitron (2021, 2022). After the fix 18 of the 19 sum to the filing total within 1%; CHS 2015 is
    within 1.1%. The fix applies only where the filing has a total and the error is exactly 1,000.
  - 1 row: Rigetti 2022 Europe is tagged 1,965,000,000 for $1,965 thousand in a filing whose other row is right. It was
    divided by 1,000.
- **Filings the corrections leave alone.** Cosmos Group 2023: the Hong Kong row (3,102,101) is interest income of a
  discontinued lending segment and the continuing revenue is 618,197, so the used rows sum to the 10-K's table total of
  3,720,298, which is also the reference. Three filings with two to four small rows against a large total (Smith & Wesson
  2024, Copano 2011, Socket Mobile 2012) are partial tables and stay as built.
- **What was not searched.** A text scan of about 1,650 filings whose used rows did not sum to their total, or that had no
  total (`code/tests/check_change_facts.py`, run on an earlier build of the table), found year-on-year changes tagged as
  levels only in Chembio. A change fact in a filing that sums to its total, or one that offsets another error, would not be
  found. A scale error with a factor other than 1,000, or in a filing with no total, would not be found either.
  `code/tests/negative_country_rows_check.csv` holds the reviewed verdict for each of the 25 rows as first found.

## Reproducing the checks

Checks 1 and 2 need a Compustat licence, so their inputs are not shared. Check 3 needs only the SEC website:
`code/build/03zc_adjudicate_fsn_geography.py --untested 180 --bench 60 --seed 11`. The reference totals need the 1.4 GB
SEC file companyfacts.zip (`code/download/01zl_sec_companyfacts.py`) and run in about five minutes
(`code/build/03zbb_fsn_geography_reference.py`).
