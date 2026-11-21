#!/usr/bin/env python3
"""Write the public release of XBRL revenue by geography: a folder ready to push to GitHub as its own repository.

WHY. The table rebuilt from SEC Financial Statement and Notes data is free of licence terms (SEC filings are US
government works), so it can be shared. Compustat cannot: this script reads only the table built with no Compustat
input (`build/03zb_validate_fsn_geography.py --public`), never `fsn_geo_validation.parquet`, and carries no GVKEY.

Outputs (60_release/xbrl-geo-revenue/)
    data/geo_revenue.csv, .parquet       one row per filing x geographic member, every row kept, nested rows flagged
    data/filing_summary.csv, .parquet    one row per filing: total revenue, rows used, domestic share, countries named
    data/region_countries.csv            the ISO3 codes each region name holds (Atlas classifications, CC0)
    data/corrections.csv                 the rows changed after the build, old and new value (03zda_release_corrections.py)
    data/COVERAGE.md                     filings and firms by fiscal year
    code/                                the scripts as used, copied from this pipeline
Hand-written files in the folder (README, VALIDATION, licences) are left alone.
Usage
    python download/01zl_sec_companyfacts.py
    python build/03zbb_fsn_geography_reference.py
    python build/03zb_validate_fsn_geography.py --public
    python build/03zd_release_fsn_geography.py
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config, regions  # noqa: E402

I = config.INTERIM
OUT = config.ROOT / "60_release" / "xbrl-geo-revenue"
PKG = Path(__file__).resolve().parent.parent
CODE = ["clean/02zr_fsn_geography.py", "build/03zb_validate_fsn_geography.py", "build/03zbb_fsn_geography_reference.py",
        "build/03zc_adjudicate_fsn_geography.py", "build/03zd_release_fsn_geography.py", "build/03zda_release_corrections.py",
        "download/01zl_sec_companyfacts.py", "core/regions.py", "core/geo.py", "tests/test_fsn_geography.py"]
FORBIDDEN = {"gvkey", "sale", "total_cs", "rows_cs", "countries_cs"}   # Compustat fields never leave the pipeline


def main() -> int:
    r = pd.read_parquet(I / "fsn_geo_revenue_public.parquet")
    print(f"rows in: {len(r):,} from fsn_geo_revenue_public.parquet")
    assert not FORBIDDEN & set(r.columns), "a Compustat field reached the release table"
    f = pd.read_parquet(I / "fsn_geo_facts.parquet", columns=["adsh", "name", "form", "period"]).drop_duplicates("adsh")
    r = r.merge(f, on="adsh", how="left", validate="many_to_one")
    r["period_end"] = pd.to_datetime(r.period.astype(str), format="%Y%m%d", errors="coerce").dt.date
    r = r.rename(columns={"name": "company", "fyear": "fiscal_year", "ref_musd": "filing_revenue_musd"})
    r["nested"] = r.nested.fillna("")
    cols = ["cik", "company", "adsh", "form", "fiscal_year", "period_end", "axis", "member", "member_words", "kind",
            "iso3", "state_table", "nested", "revenue_musd", "filing_revenue_musd"]
    r = r[cols].sort_values(["cik", "fiscal_year", "adsh", "member"], kind="mergesort").reset_index(drop=True)

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
    s["domestic_share"] = np.where(s.has_domestic.fillna(False).astype(bool), s.domestic_revenue_musd / s.filing_revenue_musd, np.nan)
    s["domestic_revenue_musd"] = s.domestic_revenue_musd.where(s.has_domestic.fillna(False).astype(bool))
    s = s.drop(columns="has_domestic").sort_values(["cik", "fiscal_year", "adsh"], kind="mergesort")

    names = sorted(set(r.loc[r.kind.isin(["region", "foreign_total"]), "member"]))
    rc = pd.DataFrame({"member": names})
    held = [regions.holds(m) for m in names]
    rc["iso3_held"] = [";".join(sorted(h)) if h is not None else "" for h in held]
    rc["n_countries"] = [len(h) if h is not None else np.nan for h in held]
    rc["read"] = [h is not None for h in held]

    d = OUT / "data"
    d.mkdir(parents=True, exist_ok=True)
    r.to_csv(d / "geo_revenue.csv", index=False)
    r.to_parquet(d / "geo_revenue.parquet", index=False)
    s.to_csv(d / "filing_summary.csv", index=False)
    s.to_parquet(d / "filing_summary.parquet", index=False)
    rc.to_csv(d / "region_countries.csv", index=False)

    cov = s.groupby("fiscal_year").agg(filings=("adsh", "size"), firms=("cik", "nunique"),
                                       sums_to_total=("sums_to_filing_revenue", "mean"),
                                       names_a_country=("countries_named", lambda c: (c > 0).mean()),
                                       has_domestic=("domestic_share", lambda v: v.notna().mean()))
    lines = ["# Coverage by fiscal year", "",
             "Fiscal year as Compustat defines it: a period ending January to May belongs to the previous year.", "",
             "| fiscal year | filings | firms (CIK) | rows sum to filing revenue (1%) | names a country | reports a domestic row |",
             "|:--|--:|--:|--:|--:|--:|"]
    for g in cov.itertuples():
        lines.append(f"| {g.Index} | {g.filings:,} | {g.firms:,} | {g.sums_to_total:.0%} | {g.names_a_country:.0%} | {g.has_domestic:.0%} |")
    lines += ["", f"The last fiscal year ({cov.index.max()}) is partial: {int(cov.filings.iloc[-1]):,} filings so far."]
    lines += ["", f"Total: {len(s):,} filings, {s.cik.nunique():,} firms, {len(r):,} rows ({(r.nested == '').sum():,} used, "
              f"{(r.nested != '').sum():,} flagged as nested). Region names read into countries: {rc.read.sum():,} of {len(rc):,}."]
    (d / "COVERAGE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Row-level corrections found by the outside check (see 03zda); they rewrite the tables written above.
    spec = importlib.util.spec_from_file_location("release_corrections", Path(__file__).with_name("03zda_release_corrections.py"))
    corr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(corr)
    if corr.main():
        raise SystemExit("03zda_release_corrections.py failed")

    c = OUT / "code"
    for p in CODE:
        dst = c / p
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PKG / p, dst)
    for p in OUT.rglob("*"):
        if p.is_file() and p.suffix in {".csv", ".parquet"}:
            assert "gvkey" not in pd.read_csv(p, nrows=0).columns if p.suffix == ".csv" else "gvkey" not in pd.read_parquet(p).columns
    print(f"rows out: geo_revenue {len(r):,}, filing_summary {len(s):,}, region_countries {len(rc):,}; "
          f"{len(CODE)} code files; written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
