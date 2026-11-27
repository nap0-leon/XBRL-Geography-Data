# XBRL Geographic Revenue: US public firms' revenue by country and region, 2011-2025

A free, open panel of how much revenue US-listed firms report earning in each country and region, rebuilt from the
SEC's Financial Statement and Notes data. It covers 23,072 annual reports (10-K) from 3,404 firms, fiscal 2010 to
2026, with every value traced to the filing it came from.

Compustat geographic segments are the usual source for this information. They require a paid licence, and a
standard subscription carries first-reported values only from fiscal 2020. This dataset is free, it goes back to
2011, and it agrees with Compustat in 90.9% of firm-years held out while the rules were built (see
[VALIDATION.md](VALIDATION.md)).

## Quick start

```python
import pandas as pd
geo = pd.read_parquet("data/geo_revenue.parquet")          # or read_csv("data/geo_revenue.csv")
used = geo[geo.nested == ""]                               # drop rows that double count
china = used[used.iso3 == "CHN"]                           # firms reporting China revenue
```

```r
geo <- arrow::read_parquet("data/geo_revenue.parquet")
used <- subset(geo, nested == "")
```

```stata
import delimited using "data/geo_revenue.csv", clear stringcols(1 3)
keep if missing(nested)
```

Link to other data by `cik` (SEC Central Index Key) or `adsh` (accession number). To open the filing behind any row,
go to `https://www.sec.gov/Archives/edgar/data/<cik>/<adsh without dashes>/`.

## Files

| file | one row per | rows |
|:--|:--|--:|
| `data/geo_revenue.csv`, `.parquet` | filing x geographic member | 92,548 |
| `data/filing_summary.csv`, `.parquet` | filing | 23,072 |
| `data/region_countries.csv` | region name | 1,821 |
| `data/corrections.csv` | row changed after the build | 93 |
| `data/COVERAGE.md` | fiscal year | |
| `code/` | the scripts that built the data, as used | |

### `geo_revenue`

| column | meaning |
|:--|:--|
| `cik` | SEC Central Index Key of the filer |
| `company` | filer name as the SEC records it |
| `adsh` | accession number of the 10-K |
| `form` | `10-K`, `10-K/A` or `10-KT` |
| `fiscal_year` | Compustat convention: a period ending January to May belongs to the previous year |
| `period_end` | fiscal period end date |
| `axis` | XBRL axis the values sit on (almost always `Geographical`) |
| `member` | XBRL member name as filed, without its namespace (`China`, `EMEA`, `US`, `CA`) |
| `member_words` | the member name split into words |
| `kind` | `domestic` (US or country of domicile), `country`, `region`, `foreign_total` (all non-US), `state` (US state) |
| `iso3` | ISO 3166 alpha-3 code for `kind == country` |
| `state_table` | the table lists US states, not countries (resolves `CA` as California) |
| `nested` | blank for a row to use; otherwise why the row double counts: `subtotal`, `duplicate`, `part_of_listed_parent`, `parent_by_sales`, `part_of_domestic`, `change_not_level` (the fact is a year-on-year change, not a revenue level) |
| `revenue_musd` | revenue in millions of US dollars, as first reported |
| `filing_revenue_musd` | the filing's own total revenue, the reference the rows are checked against |

No row is ever dropped. A row that double counts keeps its value and gains a reason in `nested`.

### `filing_summary`

One row per filing with `filing_revenue_musd`, `rows_used`, `sum_used_musd`, `coverage` (sum of used rows over
filing revenue), `sums_to_filing_revenue` (within 1%), `domestic_revenue_musd` and `domestic_share` (blank when the
filing reports no domestic row), and `countries_named`.

### `region_countries`

The ISO3 codes each region name holds, for example `EMEA`, `AsiaPacific` or `EuropeExcludingUnitedKingdom`.
Membership comes from the Harvard Growth Lab country groups (CC0). `read == False` marks a name no rule can read
(`Other`, `SegmentGeographicalGroupsOfCountriesGroupOne`). Those hold an unknown set of countries, never an empty one.

## A note on self-reporting

- **Data as reported.** Each firm chooses its own split. US rules (ASC 280) require revenue for the country of domicile
  and for each material foreign country. Many firms report regions (`Europe`, `Rest of world`) and few countries.
  46.7% of filings (10,774 of 23,072) name at least one foreign country. This is a panel of reported geography, not a full
  firm-by-country matrix.
- **Who is in it.** Firms that tag revenue by geography in XBRL: about 1,500 to 1,700 a year from 2012, weighted
  toward large multinationals. Firms with one market often report no geographic table at all.
- **Revenue by customer location, mostly.** Most firms report by customer location, and some by the location of
  the selling entity. The filing says which; the data does not.
- **First reported.** Values are as first filed, never restated. Use them for event studies and real-time exposure.
- **Negative values.** 310 used rows carry negative revenue, mostly intersegment eliminations classed as regions.
  They make the used rows sum to the filing total. Drop rows whose member contains 'Elimination' before you compute
  country or regional shares.
- **Thin edges.** Fiscal 2010 and 2011 are partial, because smaller filers began detailed XBRL tagging of notes in
  2011. Fiscal 2026 is a stub (84 filings). 55 filings up to 2025 have no total revenue fact of their own, so
  `filing_revenue_musd` is blank there.

## A note on validation

The rules were tuned on fiscal 2020-2022 and frozen before fiscal 2023-2026 were examined. On those held-out 4,706
firm-years, the data agrees with Compustat first-reported geographic segments in 90.9% and clearly differs in 0.8%.
The rest splits geography differently or reports only part of it. Where the two disagree, the 10-K prints this
dataset's numbers more often than Compustat's (75% against 64%). Fiscal 2011-2019, which Compustat cannot check,
reproduce the 10-K about as often as the validated years (86% against 88% of sampled filings, a difference well
inside one standard error). Details in [VALIDATION.md](VALIDATION.md).

## Use cases

1. **Firm exposure to a foreign market.** Share of revenue from China, Mexico, the UK or Europe for event studies of
   tariffs, sanctions, elections, pandemics and currency moves.
2. **Firm-specific exchange-rate indices.** Weight currencies by each firm's reported revenue mix to measure exposure
   that a broad dollar index misses.
3. **Foreign demand shocks.** Combine country weights with foreign GDP, import demand or policy shocks to build
   shift-share exposure measures at the firm level.
4. **Checking text-based exposure.** Compare measures built from earnings calls or 10-K text with the revenue firms
   actually report.
5. **Disclosure research.** Study how firms choose geographic detail: which countries they name, when they move from
   countries to regions, and how the choice responds to competition or regulation.
6. **Multinational activity over time.** Track the domestic share of revenue and the rise or fall of particular
   markets across a fixed set of firms since 2011.
7. **Teaching and replication.** A licence-free substitute for Compustat segments in classes and replication
   packages that cannot share proprietary data.

## Replications this makes possible now

The designs below rely on firm revenue by country. Each can be run with this data alone, with stock returns from
any source.

| study | what it needs from this data | coverage here |
|:--|:--|:--|
| Jorion (1990), exchange-rate exposure of US multinationals, *Journal of Business* | foreign share of revenue | all years; any currency with country rows |
| Ramelli and Wagner (2020), stock reactions to COVID-19, *Review of Corporate Finance Studies* | China exposure before 2020 | 188 firms report China revenue in fiscal 2019 |
| Huang, Lin, Liu and Tang (2023), US-China trade war and firm value, *Journal of International Economics* | China revenue share in 2017-2018 | 184-193 firms a year report China revenue |
| Brexit referendum event study (June 2016) | UK revenue share in fiscal 2015 | 216 firms report UK revenue |
| Hassan, Schreger, Schwedeler and Tahoun, country risk from earnings calls, *Review of Economic Studies* | reported revenue by country to compare with text exposure | all years |
| Berger and Hann (2003), segment disclosure under SFAS 131, *Journal of Accounting Research* | the fineness of geographic disclosure | XBRL era only |

A design that needs small markets will find few firms: 12 firms report Russia revenue in fiscal 2021.

## Coverage

See [data/COVERAGE.md](data/COVERAGE.md). Most named countries by row count: Canada, United Kingdom, China, Germany,
Japan, Mexico, Australia, Brazil, South Korea, France, Taiwan, Italy.

## Roadmap

- **Firm-to-firm customer links.** US rules require firms to disclose customers above 10% of revenue. Many tag these
  in XBRL on the major-customers axis. The same pipeline can extract them into a free version of the Compustat
  customer segment file.
- **Assets by country.** The extraction already keeps long-lived assets by geography. A later release can add them.
- **v1.1: add kind = elimination.**
- **Annual updates.** Each new SEC quarter adds filings! 

## How it was built

1. Download each quarterly SEC Financial Statement and Notes file (2009q1 onward), keep every revenue fact on one
   geographic axis, and delete the raw file (`code/clean/02zr_fsn_geography.py`).
2. Find each filing's own total revenue in the SEC companyfacts file, by accession number
   (`code/download/01zl_sec_companyfacts.py`, `code/build/03zbb_fsn_geography_reference.py`). This is the reference
   that the rows are checked against.
3. Classify members (domestic, country, region, state), resolve two-letter codes that are both a country and a state,
   choose one revenue tag per filing, and flag rows that double count (`code/build/03zb_validate_fsn_geography.py`).
4. Score values against the numbers printed in the 10-K (`code/build/03zc_adjudicate_fsn_geography.py`).
5. Write the release and apply 93 row corrections found by an outside check (signs, year-on-year changes tagged as
   levels, 1,000-fold scale errors), each logged in `data/corrections.csv`
   (`code/build/03zd_release_fsn_geography.py`, `code/build/03zda_release_corrections.py`; see
   [VALIDATION.md](VALIDATION.md)).

The scripts run inside the research pipeline they came from (paths and downloads go through `core.config` and
`core.fetch`). They document every rule. They are not a standalone package.

## Citation

See [CITATION.cff](CITATION.cff). GitHub shows a "Cite this repository" button once it is pushed.

## Licence

Data: [CC BY 4.0](LICENSE-DATA.md). Code: [MIT](LICENSE). The source filings are US government works.
The country groups come from the Harvard Growth Lab classifications (doi:10.7910/DVN/3BAL1O, CC0).
This dataset contains no Compustat data. Compustat was used only to measure agreement, and only aggregate rates are
reported.
