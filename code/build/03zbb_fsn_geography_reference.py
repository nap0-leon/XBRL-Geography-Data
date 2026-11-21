#!/usr/bin/env python3
"""Find each geography filing's own annual revenue in SEC companyfacts, for the reference total of 03zb.

WHY. `03zb_validate_fsn_geography.py` compares the geography rows of a filing with the filing's total revenue (the
reference). It took that total from `sec_firm_year` by accession number, and by CIK and fiscal year when the accession
number was not there. The fallback returned another filing's revenue: an 8-K with recast figures (Visteon 2014), the
prior year (Karbon-X 2025, Mantra Venture 2016), or a different fact (EACO 2012). A wrong reference breaks the nesting
step, which sets aside region totals that sum from their own countries, so Visteon's Asia and Europe counted twice.

WHAT. companyfacts.zip holds every undimensioned XBRL fact of a CIK with its accession number (`accn`), period
(`start`, `end`) and form. For each filing this script keeps the annual revenue facts of that accession whose period
ends within 10 days of the filing's period end (a 52 or 53 week year ends on a Saturday) and lasts 350 to 380 days, under the revenue tags of 03zb (`REVENUE` and `EXTERNAL`
plus the tags `sec_firm_year` uses). It then records four candidates:

    ref_priority   the first tag in the order of `TAGS`
    ref_max        the largest value among the tags of the filing
    ref_own        the value in sec_firm_year for the filing's own accession number (the priority tag of 02c_clean_sec)
    ref_stored     the value 03zb used so far: ref_own, else sec_firm_year by CIK and fiscal year (the fallback)

and the reference `ref_musd` that `03zb_validate_fsn_geography.py` reads from `fsn_geo_reference_totals.parquet`
(rule `hybrid`): first, of two revenue values of one filing that are 1,000 or 1,000,000 times apart, one is dropped
(a filer's scale error; `drop_scale_duplicates` keeps the one that agrees with the stored value); then ref_own, unless it is below `HALF` of ref_max (EACO 2012: 1.25 against 113.4 million); then ref_max;
and when the filing has no ref_own, ref_max. The fallback by CIK and fiscal year no longer supplies a reference; it only breaks
a scale tie. A filing with neither has no reference, as before.

EVIDENCE. Four rules were scored on the filings linked to Compustat, by the share whose reference is within 5% of
Compustat sales (Compustat only scored the rules; none of its values is in the output). The rule was chosen on the tuning
years (fiscal 2020-2022) and checked on the years after: tuning 4,902 filings, stored rule 97.8%, ref_priority 98.1%,
ref_max 98.3%, hybrid 98.5%; held out 2023 on, 4,904 filings, 95.4%, 97.5%, 97.9%, 98.2%; fiscal 2011-2019, 12,322
filings, 96.4%, 96.9%, 97.9%, 97.9%. (The scale-duplicate step came after the first scoring, from the Tigo Energy case
and then the Quaker Chemical and CIRCOR cases; these are the figures with it.) The threshold `HALF` = 0.5 is a round number picked after a scan of 0.3 to 0.9 on all years, which moved the
result by a few filings; the scan came before the split, so the held-out figures are a check on the rule, not a clean test of 0.5. Compustat sales are a yardstick with their own definitions (net of
excise tax, for example), so within 5% of them is not the same as correct.
No classification rule changes: only the reference total that the frozen rules read.

Outputs
    20_data/interim/fsn_geo_reference_candidates.parquet   adsh, tag, value_musd, start, end, form
    20_data/interim/fsn_geo_reference_totals.parquet       adsh, cik, ref_musd, ref_tag, ref_priority_musd, ref_max_musd, ref_stored_musd
    40_output/tables/fsn_geography_reference.md
Usage
    python download/01zl_sec_companyfacts.py
    python build/03zbb_fsn_geography_reference.py            # reads the 1.4 GB zip, about 5 minutes
    python build/03zbb_fsn_geography_reference.py --reuse    # keeps the candidate facts, redoes the reference rule
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config  # noqa: E402

I = config.INTERIM
ZIP = config.RAW / "sec" / "companyfacts.zip"
OUT_C = I / "fsn_geo_reference_candidates.parquet"
OUT_T = I / "fsn_geo_reference_totals.parquet"
REPORT = config.TABLES / "fsn_geography_reference.md"

# Order of preference, as in 02c_clean_sec.CONCEPT_TAGS["sales"], then the tags 03zb reads for geography.
TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet",
        "SalesRevenueServicesNet", "SalesRevenueGoodsAndServicesNet", "RevenuesNetOfInterestExpense", "OperatingRevenue",
        "RevenuesExcludingInterestAndDividends", "SegmentReportingInformationRevenueFromExternalCustomers",
        "RevenueFromExternalCustomers"]
RULE = "hybrid"       # chosen on Compustat agreement (see the docstring); "max" and "priority" are the other candidates
HALF = 0.5            # ref_own is kept when it is at least this share of ref_max
DAYS = (350, 380)     # an annual period, including 52 and 53 week years
SLACK = 10            # days between the fact's end and the filing's period (month-end) for a 52 or 53 week year


def candidates() -> pd.DataFrame:
    f = pd.read_parquet(I / "fsn_geo_facts.parquet", columns=["adsh", "cik", "period", "form"]).drop_duplicates("adsh")
    f["cik"] = f.cik.astype(str).str.lstrip("0")
    f["period"] = pd.to_datetime(f.period.astype(str), format="%Y%m%d", errors="coerce").dt.date.astype(str)
    want = {c: g.set_index("adsh").period.to_dict() for c, g in f.groupby("cik")}
    rows = []
    with zipfile.ZipFile(ZIP) as z:
        names = set(z.namelist())
        for n, (cik, per) in enumerate(want.items(), 1):
            member = f"CIK{int(cik):010d}.json"
            if member not in names:
                continue
            tax = json.loads(z.read(member)).get("facts", {}).get("us-gaap", {})
            for tag in TAGS:
                for fact in tax.get(tag, {}).get("units", {}).get("USD", []):
                    a = fact.get("accn")
                    if a in per and fact.get("start") and fact.get("end"):
                        end = dt.date.fromisoformat(fact["end"])
                        d = (end - dt.date.fromisoformat(fact["start"])).days
                        if DAYS[0] <= d <= DAYS[1] and abs((end - dt.date.fromisoformat(per[a])).days) <= SLACK:
                            rows.append((a, int(cik), tag, fact["val"] / 1e6, fact["start"], fact["end"], fact.get("form")))
            if n % 500 == 0:
                print(f"  {n:,}/{len(want):,} CIKs, {len(rows):,} facts", flush=True)
    c = pd.DataFrame(rows, columns=["adsh", "cik", "tag", "value_musd", "start", "end", "form"])
    return c.drop_duplicates(["adsh", "tag", "value_musd"])


def filings() -> pd.DataFrame:
    """One row per filing of fsn_geo_facts with its CIK and fiscal year (Compustat convention, as in 03zb)."""
    f = pd.read_parquet(I / "fsn_geo_facts.parquet", columns=["adsh", "cik", "period"]).drop_duplicates("adsh")
    dd = pd.to_datetime(f.period.astype(str), format="%Y%m%d", errors="coerce")
    return f.assign(cik=f.cik.astype(str).str.lstrip("0"), fyear=np.where(dd.dt.month <= 5, dd.dt.year - 1, dd.dt.year))


def stored_reference() -> pd.DataFrame:
    """The reference 03zb used before this step (stored) and its own-accession part (own)."""
    s = pd.read_parquet(I / "sec_firm_year.parquet", columns=["cik", "fy", "adsh", "sales"]).dropna(subset=["sales"])
    by_adsh = dict(zip(s.adsh, s.sales / 1e6))
    by_cik = dict(zip(zip(s.cik.astype(str), s.fy.astype(int)), s.sales / 1e6))
    f = filings()
    own = pd.Series({a: by_adsh.get(a, np.nan) for a in f.adsh})
    stored = pd.Series({a: by_adsh.get(a, by_cik.get((str(c), int(y)), np.nan)) for a, c, y in f[["adsh", "cik", "fyear"]].itertuples(index=False)})
    return pd.DataFrame({"own": own, "stored": stored})


def is_scale_pair(v: float, w: float) -> bool:
    """True when v and w differ by a factor of 1,000 or 1,000,000 (either way round)."""
    if v <= 0 or w <= 0:
        return False
    r = max(v, w) / min(v, w)
    return any(abs(r / f - 1) < 0.01 for f in (1e3, 1e6))


def drop_scale_duplicates(c: pd.DataFrame, ref: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Within one filing, of two revenue values 1,000 or 1,000,000 times apart, keep one: the filer made a scale error.

    Value alone cannot say which is right: Tigo Energy 2024 tags `Revenues` 54.014 million and another tag 54,014
    million (the larger is wrong), Quaker Chemical 2018 tags 867.52 and 0.862 (the smaller is wrong). The tie is broken by
    the value `sec_firm_year` stored for the filing (`ref.stored`), and only between two candidates of the same filing; when
    it matches neither, the value under the tag earlier in `TAGS` is kept. The own value of `sec_firm_year` takes part as
    the first tag."""
    order = {t: k + 1 for k, t in enumerate(TAGS)}
    own, stored = ref.own.copy(), ref.stored
    drop, own_lost = set(), set()
    for a, g in c.groupby("adsh"):
        items = [(i, v, order[t]) for i, v, t in zip(g.index, g.value_musd, g.tag)]
        if pd.notna(own.get(a, np.nan)):
            items.append(("own", own[a], 0))
        st = stored.get(a, np.nan)
        for x in range(len(items)):
            for y in range(x + 1, len(items)):
                (i, v, p), (j, w, q) = items[x], items[y]
                if i in drop or j in drop or not is_scale_pair(v, w):
                    continue
                if pd.notna(st) and abs(v / st - 1) < 0.01:
                    lose = j
                elif pd.notna(st) and abs(w / st - 1) < 0.01:
                    lose = i
                else:
                    lose = j if p <= q else i
                (own_lost if lose == "own" else drop).add(a if lose == "own" else lose)
    for a in own_lost:
        own[a] = np.nan
    return c.drop(index=list(drop)), own


def totals(c: pd.DataFrame, ref: pd.DataFrame, cik: dict | None = None) -> pd.DataFrame:
    c, own = drop_scale_duplicates(c, ref)
    ref = ref.assign(own=own)
    order = {t: k for k, t in enumerate(TAGS)}
    c = c.assign(prio=c.tag.map(order))
    pri = c.sort_values(["adsh", "prio"]).drop_duplicates("adsh").set_index("adsh")
    mx = c.sort_values(["adsh", "value_musd", "prio"], ascending=[True, False, True]).drop_duplicates("adsh").set_index("adsh")
    t = pd.DataFrame({"adsh": ref.index})
    t["ref_priority_musd"] = t.adsh.map(pri.value_musd)
    t["ref_max_musd"] = t.adsh.map(mx.value_musd)
    t["ref_own_musd"] = t.adsh.map(ref.own)
    t["ref_stored_musd"] = t.adsh.map(ref.stored)
    if RULE == "hybrid":
        keep = t.ref_own_musd.notna() & (t.ref_max_musd.isna() | (t.ref_own_musd >= HALF * t.ref_max_musd))
        t["ref_musd"] = np.where(keep, t.ref_own_musd, t.ref_max_musd)
        t["ref_tag"] = np.where(keep, "sec_firm_year", t.adsh.map(mx.tag))
    else:
        pick = {"max": ("ref_max_musd", mx), "priority": ("ref_priority_musd", pri)}[RULE]
        t["ref_musd"] = t[pick[0]]
        t["ref_tag"] = t.adsh.map(pick[1].tag)
    t["cik"] = t.adsh.map(cik or {})
    return t[["adsh", "cik", "ref_musd", "ref_tag", "ref_priority_musd", "ref_max_musd", "ref_own_musd", "ref_stored_musd"]]


def main() -> int:
    if not ZIP.exists():
        raise SystemExit(f"missing {ZIP}: run download/01zl_sec_companyfacts.py")
    if "--reuse" in sys.argv and OUT_C.exists():
        c = pd.read_parquet(OUT_C)
        print(f"reusing {OUT_C.name}: {len(c):,} candidate facts")
    else:
        c = candidates()
        c.to_parquet(OUT_C, index=False)
    f = filings()
    t = totals(c, stored_reference(), dict(zip(f.adsh, f.cik)))
    t.to_parquet(OUT_T, index=False)
    n = len(t)
    chg = (t.ref_musd / t.ref_stored_musd - 1).abs() > 0.005
    lines = ["# Reference totals of the geography filings", "",
             f"- filings: {n:,}; with an own annual revenue fact in companyfacts: {t.ref_max_musd.notna().sum():,}",
             f"- rule in use: `{RULE}`; stored reference present for {t.ref_stored_musd.notna().sum():,}",
             f"- new reference differs from the stored one by more than 0.5%: "
             f"{chg.sum():,}; stored present and new missing: "
             f"{(t.ref_stored_musd.notna() & t.ref_musd.isna()).sum():,}; stored missing and new present: "
             f"{(t.ref_stored_musd.isna() & t.ref_musd.notna()).sum():,}"]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
