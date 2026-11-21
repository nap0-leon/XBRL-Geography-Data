#!/usr/bin/env python3
"""Row-level corrections to the public geography release, found by an outside check before v1.0.0.

WHY. An outside check of data/geo_revenue.parquet found used rows with negative revenue. The 10-Ks of the 25 negative
`country` and `domestic` rows were read (`code/tests/check_negative_printed.py`, then by hand), a text scan of the filings
that do not sum to their total found more of the same cause (`code/tests/check_change_facts.py`), and the filing-level
`coverage` (used rows over the filing's total revenue) showed rows 1,000 times off. The classification rules stay as
frozen on 2026-10-27; this is a list of rows, each with the filing text behind it.

  flip       the 10-K prints the value as positive and the XBRL fact carries a minus sign
             L-3 Communications FY2010 (7 rows), Cytosorbents FY2017 Germany
  set aside  the XBRL fact is a year-on-year change in sales, not a revenue level. The row keeps its value and gains
             nested = "change_not_level", like every other row set aside. Chembio Diagnostics FY2013, FY2015, FY2016
  rescale    the used rows of a filing sum to 1,000 or 0.001 times the filing's own total revenue: the filer tagged
             thousands as dollars or the reverse. Every row of the filing is multiplied or divided by 1,000
  one row    Rigetti FY2022 Europe is tagged 1,965,000,000 for $1,965 thousand, in a filing whose other row is right

A row the pipeline already treats the same way (set aside under another reason) is skipped and listed. The reference
totals are not corrected here: `build/03zbb_fsn_geography_reference.py` takes them from SEC companyfacts, and the rules of
03zb read them from there.

Inputs   data/geo_revenue.parquet, data/filing_summary.parquet (written by 03zd_release_fsn_geography.py)
Outputs  the same two tables and their CSVs, data/corrections.csv (the old and new value of every changed row) and the
         table rows and totals line of data/COVERAGE.md
03zd runs this step after it writes the release. Run it alone only on a fresh 03zd output: a second run stops at the first
row whose value is no longer the one expected. The summary columns are rebuilt with the formulas of 03zd, and the rebuild
is first checked to reproduce the written summary.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_here = Path(__file__).resolve()
try:                                  # inside the pipeline
    sys.path.insert(0, str(_here.parent.parent))
    from core import config
    D = config.ROOT / "60_release" / "xbrl-geo-revenue" / "data"
except ImportError:                   # the copy in the release: code/build/ next to data/
    D = _here.parents[2] / "data"

FLIP = [("0000950123-11-017756", m) for m in ["Australia", "Canada", "China", "Germany", "Italy", "SouthKorea", "UnitedStates"]] \
    + [("0001144204-18-013618", "DE")]
SET_ASIDE = [
    ("0001092662-16-000121", "Mexico"), ("0001092662-17-000023", "BR"), ("0001092662-17-000023", "NG"),
    ("0001092662-17-000023", "US"), ("0001092662-17-000023", "Uganda"),
    # the same cause, read in the Chembio 10-Ks: MD&A changes tagged on the geography axis
    ("0001092662-17-000023", "Mexico"),       # "increased sales to Mexico of $123,200"
    ("0001092662-17-000023", "EURODELTA"),    # "European sales decreased by $68,600"
    ("0001092662-17-000023", "MiddleEast"),   # the Asia increase of $55,300
    ("0001092662-16-000121", "MiddleEast"),   # the Asia increase of $76,200
    ("0001092662-14-000018", "Uganda"),       # "increased sales in Uganda by approximately $1,236,000"
    ("0001092662-14-000018", "ET"),           # "Ethiopia by approximately $262,000"
    ("0001092662-14-000018", "MiddleEast"),   # "decreased sales in the Middle East by $483,000" (tagged 483,000 million)
]
NEW_REASON = "change_not_level"
# (coverage low, coverage high, factor, action). CHS 2014 and 2015 sit at 0.00099 because of rounding.
SCALE_BANDS = [(0.00098, 0.00102, 1000.0, "scale_x1000"), (980.0, 1020.0, 0.001, "scale_div1000")]
SCALE_ROWS = [("0001193125-23-080486", "Europe", 0.001)]   # (adsh, member, factor)


def summarize(r: pd.DataFrame, old: pd.DataFrame) -> pd.DataFrame:
    """Same formulas as 03zd_release_fsn_geography.py, from the release table."""
    used = r[(r.nested == "") & (r.kind != "state")]
    s = used.groupby("adsh").agg(rows_used=("member", "size"), sum_used_musd=("revenue_musd", "sum"),
                                 domestic_revenue_musd=("revenue_musd", lambda v: v[used.loc[v.index, "kind"] == "domestic"].sum()),
                                 has_domestic=("kind", lambda k: (k == "domestic").any()),
                                 countries_named=("kind", lambda k: int((k == "country").sum())))
    head = r.drop_duplicates("adsh").set_index("adsh")[["cik", "company", "form", "fiscal_year", "period_end", "filing_revenue_musd"]]
    s = head.join(s, how="left").reset_index()
    s["rows_used"] = s.rows_used.fillna(0).astype(int)
    s["countries_named"] = s.countries_named.fillna(0).astype(int)
    s["coverage"] = s.sum_used_musd / s.filing_revenue_musd
    s["sums_to_filing_revenue"] = (s.coverage - 1).abs() <= 0.01
    hd = s.has_domestic.fillna(False).astype(bool)
    s["domestic_share"] = np.where(hd, s.domestic_revenue_musd / s.filing_revenue_musd, np.nan)
    s["domestic_revenue_musd"] = s.domestic_revenue_musd.where(hd)
    s = s.drop(columns="has_domestic").sort_values(["cik", "fiscal_year", "adsh"], kind="mergesort")
    return s[old.columns].reset_index(drop=True)


def update_coverage(s: pd.DataFrame, r: pd.DataFrame) -> None:
    """Rewrite the table rows and the totals line of data/COVERAGE.md with the same formulas as 03zd."""
    p = D / "COVERAGE.md"
    cov = s.groupby("fiscal_year").agg(filings=("adsh", "size"), firms=("cik", "nunique"),
                                       sums_to_total=("sums_to_filing_revenue", "mean"),
                                       names_a_country=("countries_named", lambda c: (c > 0).mean()),
                                       has_domestic=("domestic_share", lambda v: v.notna().mean()))
    new = {str(g.Index): f"| {g.Index} | {g.filings:,} | {g.firms:,} | {g.sums_to_total:.0%} | {g.names_a_country:.0%} | "
                         f"{g.has_domestic:.0%} |" for g in cov.itertuples()}
    out = []
    for line in p.read_text(encoding="utf-8").split("\n"):
        cells = [c.strip() for c in line.split("|")]
        if line.startswith("| 20") and len(cells) > 2 and cells[1] in new:
            line = new[cells[1]]
        line = re.sub(r"\(([\d,]+) used, ([\d,]+) flagged as nested\)",
                      f"({(r.nested == '').sum():,} used, {(r.nested != '').sum():,} flagged as nested)", line)
        out.append(line)
    p.write_text("\n".join(out), encoding="utf-8")


def main() -> int:
    r = pd.read_parquet(D / "geo_revenue.parquet")
    old = pd.read_parquet(D / "filing_summary.parquet")
    pd.testing.assert_frame_equal(summarize(r, old), old.reset_index(drop=True), check_dtype=False, rtol=1e-9)
    print("summary rebuild reproduces the written filing_summary")

    log, skipped = [], []

    def put(i, adsh, member, action, new_value, new_nested):
        row = r.loc[i]
        log.append(dict(cik=row.cik, company=row.company, adsh=adsh, fiscal_year=row.fiscal_year, member=member,
                        action=action, old_revenue_musd=row.revenue_musd, new_revenue_musd=new_value,
                        old_nested=row.nested, new_nested=new_nested))
        r.at[i, "revenue_musd"], r.at[i, "nested"] = new_value, new_nested

    for adsh, member in FLIP:
        i = r.index[(r.adsh == adsh) & (r.member == member)]
        assert len(i) == 1, (adsh, member)
        row = r.loc[i[0]]
        if row.nested == "" and row.revenue_musd < 0:
            put(i[0], adsh, member, "flip_sign", -row.revenue_musd, row.nested)
        else:
            skipped.append((adsh, member, "flip", f"value {row.revenue_musd}, nested {row.nested!r}"))
    for adsh, member in SET_ASIDE:
        i = r.index[(r.adsh == adsh) & (r.member == member)]
        assert len(i) == 1, (adsh, member)
        row = r.loc[i[0]]
        if row.nested == "":
            put(i[0], adsh, member, "set_aside", row.revenue_musd, NEW_REASON)
        else:
            skipped.append((adsh, member, "set aside", f"already nested as {row.nested!r}"))

    s0 = summarize(r, old)               # coverage of the table as corrected so far
    n_filings = 0
    for lo, hi, factor, action in SCALE_BANDS:
        for adsh in s0.adsh[(s0.rows_used >= 2) & s0.coverage.between(lo, hi)]:
            n_filings += 1
            for i in r.index[r.adsh == adsh]:
                put(i, adsh, r.at[i, "member"], action, round(r.at[i, "revenue_musd"] * factor, 9), r.at[i, "nested"])
    for adsh, member, factor in SCALE_ROWS:
        i = r.index[(r.adsh == adsh) & (r.member == member)]
        assert len(i) == 1, (adsh, member)
        put(i[0], adsh, member, "scale_row_div1000", round(r.at[i[0], "revenue_musd"] * factor, 9), r.at[i[0], "nested"])

    log = pd.DataFrame(log, columns=["cik", "company", "adsh", "fiscal_year", "member", "action", "old_revenue_musd",
                                     "new_revenue_musd", "old_nested", "new_nested"])
    if log.empty:       # a second run on corrected tables: leave the tables and the log as they are
        print(f"nothing to correct ({len(skipped)} listed rows no longer match): tables and data/corrections.csv unchanged")
        return 0
    s = summarize(r, old)
    print(f"{len(log)} rows corrected ({log.action.value_counts().to_dict()}); {n_filings} filings rescaled; {len(skipped)} skipped")
    for sk in skipped:
        print("  skipped:", sk)
    r.to_csv(D / "geo_revenue.csv", index=False)
    r.to_parquet(D / "geo_revenue.parquet", index=False)
    s.to_csv(D / "filing_summary.csv", index=False)
    s.to_parquet(D / "filing_summary.parquet", index=False)
    log.to_csv(D / "corrections.csv", index=False)
    update_coverage(s, r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
