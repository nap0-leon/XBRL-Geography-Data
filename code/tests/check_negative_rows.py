"""Report negative revenue among the rows marked for use (nested == "").

Reads data/geo_revenue.parquet and data/filing_summary.parquet. Prints:
  1. counts of negative used rows by kind and by member,
  2. the full list of negative used rows with kind "country" or "domestic",
  3. the share of filings that name at least one foreign country
     (countries_named counts kind == "country" among used rows, so it excludes the domestic row).
Writes the country and domestic rows to the path given by --out, if any.
"""
import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="optional CSV path for the country and domestic negative rows")
    a = ap.parse_args()

    geo = pd.read_parquet(ROOT / "data" / "geo_revenue.parquet")
    fs = pd.read_parquet(ROOT / "data" / "filing_summary.parquet", columns=["adsh", "fiscal_year", "countries_named"])
    used = geo[geo.nested == ""]
    neg = used[used.revenue_musd < 0]
    print(f"used rows: {len(used):,}; negative used rows: {len(neg):,}; minimum: {neg.revenue_musd.min():,.1f} musd")
    print(f"negative rows with nested != '': {(geo[geo.nested != ''].revenue_musd < 0).sum():,}")
    print("\nNegative used rows by kind")
    print(neg.kind.value_counts().to_string())
    print("\nNegative used rows by member")
    print(neg.member.value_counts().to_string())
    print(f"\nNegative used rows whose member contains 'Elimination': {neg.member.str.contains('Elimination').sum():,}")

    cd = neg[neg.kind.isin(["country", "domestic"])]
    cols = ["cik", "company", "adsh", "fiscal_year", "member", "revenue_musd", "filing_revenue_musd"]
    cd = cd[cols].sort_values(["cik", "fiscal_year"])
    print(f"\nNegative used rows with kind country or domestic: {len(cd)}")
    with pd.option_context("display.width", 250, "display.max_columns", 20, "display.max_colwidth", 40):
        print(cd.to_string(index=False))
    if a.out:
        cd.to_csv(a.out, index=False)

    n = len(fs)
    share = (fs.countries_named > 0).mean()
    print(f"\nFilings: {n:,}; with countries_named > 0 (at least one foreign country): {(fs.countries_named > 0).sum():,} = {share:.1%}")
    fs26 = fs[fs.fiscal_year <= 2025]
    print(f"Fiscal 2025 and earlier only: {len(fs26):,} filings, {(fs26.countries_named > 0).mean():.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
