#!/usr/bin/env python3
"""Revenue and long-lived assets by country and region from the SEC Financial Statement and Notes data sets, 2009 on.

WHY. Compustat geographic segments start in 2018 in our subscription. The same 10-K note is tagged in XBRL from
2009 (large filers) and 2011 (all filers), on the geographic axes. This script takes those facts.

HOW. The raw notes files were evicted on 2026-10-18 (`20_data/evicted_sources.json`, 27.6 GB). Each quarter is
fetched again through core.fetch, read, written to its own part file, and deleted (signed off by Ahmed on
2026-10-27). A quarter whose part file exists is skipped, so the script resumes where it stopped.

WHAT IS KEPT. Annual facts (`qtrs` 4, or 0 for balance-sheet tags) of 10-K filings, no co-registrant rows, whose
dimensions include exactly one geographic axis (`GEO_AXES`). Other axes on the same fact are kept in `other_axes`
for the build to filter. Members keep their spelling: the files drop the namespace, so `CA` is Canada or
California until the build reads the other members of the table.

Outputs
    20_data/interim/_fsn_geo_parts/<quarter>.parquet    one per quarter file
    20_data/interim/fsn_geo_facts.parquet               all quarters: adsh, cik, name, form, fy, fp, period, ddate,
                                                        qtrs, tag, axis, member, member_words, other_axes, value, uom
    40_output/tables/fsn_geography.md
Usage
    python clean/02zr_fsn_geography.py                  # every quarter, fetch-read-delete
    python clean/02zr_fsn_geography.py --only 2019q1    # one quarter
    python clean/02zr_fsn_geography.py --combine        # only rebuild the combined file and the report
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config, fetch  # noqa: E402

KEY = "sec_fsn"
PARTS = config.INTERIM / "_fsn_geo_parts"
OUT = config.INTERIM / "fsn_geo_facts.parquet"
REPORT = config.ROOT / "40_output" / "tables" / "fsn_geography.md"
FLOOR_GB = 8.0
# The geographic axes of revenue and asset tables, as the files spell them (namespace and "Statement"/"Axis" removed),
# measured on 2019q1. Investment, real estate and loan-region axes are not revenue by market and are left out.
GEO_AXES = {"Geographical", "GeographicalAreas", "GeographicalRegions", "RegionReportingInformationByRegion",
            "Countries", "AllCountries", "CountryName", "GeographicDistribution"}
REVENUE = {"Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet", "SalesRevenueServicesNet",
           "RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax",
           "RevenuesNetOfInterestExpense", "RevenuesExcludingInterestAndDividends", "OperatingRevenue",
           "SegmentReportingInformationRevenueFromExternalCustomers", "RevenueFromExternalCustomers"}
ASSETS = {"NoncurrentAssets", "LongLivedAssets", "PropertyPlantAndEquipmentNet", "Assets",
          "LongLivedAssetsGeographicAreas", "SegmentReportingInformationAssets"}
TAGS = REVENUE | ASSETS
LINES: list[str] = []


def say(s: str = "") -> None:
    print(s, flush=True)
    LINES.append(s)


def words(member: str) -> str:
    m = re.sub(r"Member$", "", member.split(":")[-1])
    m = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", m)
    m = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", m)
    return m.strip()


def jobs() -> list[tuple[str, Path]]:
    s = importlib.util.spec_from_file_location("fsn_dl", Path(__file__).resolve().parent.parent / "download" / "01n_sec_fsn.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return sorted(m.jobs(), key=lambda j: j[1].name)


def quarter(z: zipfile.ZipFile, label: str) -> pd.DataFrame:
    sub = pd.read_csv(z.open("sub.tsv"), sep="\t", dtype=str, keep_default_na=False,
                      usecols=["adsh", "cik", "name", "form", "fy", "fp", "period"])
    sub = sub[sub.form.isin(["10-K", "10-K/A", "10-KT"])]
    dim = pd.read_csv(z.open("dim.tsv"), sep="\t", dtype=str, keep_default_na=False)
    keep = {}
    for h, sgs in zip(dim.dimhash, dim.segments):
        parts = [p for p in sgs.split(";") if "=" in p]
        geo = [p for p in parts if p.split("=")[0] in GEO_AXES]
        if len(geo) == 1:
            ax, mem = geo[0].split("=", 1)
            keep[h] = (ax, mem, ";".join(p for p in parts if p != geo[0]))
    out = []
    cols = ["adsh", "tag", "ddate", "qtrs", "uom", "dimh", "coreg", "value"]
    for ch in pd.read_csv(z.open("num.tsv"), sep="\t", dtype=str, usecols=cols, chunksize=2_000_000, keep_default_na=False):
        ch = ch[ch.dimh.isin(keep.keys()) & ch.tag.isin(TAGS) & (ch.coreg == "") & ch.adsh.isin(sub.adsh) & ch.qtrs.isin(["4", "0"])]
        if len(ch):
            out.append(ch)
    if not out:
        return pd.DataFrame(columns=["adsh", "tag", "ddate", "qtrs", "uom", "value", "axis", "member", "other_axes",
                                     "cik", "name", "form", "fy", "fp", "period", "member_words", "quarter_file"])
    n = pd.concat(out, ignore_index=True)
    n[["axis", "member", "other_axes"]] = pd.DataFrame([keep[h] for h in n.dimh], index=n.index)
    n["value"] = pd.to_numeric(n.value, errors="coerce")
    n = n.merge(sub, on="adsh", how="left").drop(columns=["dimh", "coreg"])
    n["member_words"] = n.member.map(words)
    n["quarter_file"] = label
    return n


def run_one(url: str, raw: Path) -> str:
    label = raw.name.replace("_notes.zip", "")
    part = PARTS / f"{label}.parquet"
    if part.exists():
        if raw.exists():
            raw.unlink()
        return f"- {label}: part on disk, skipped"
    if shutil.disk_usage(config.RAW).free / 1e9 < FLOOR_GB:
        raise SystemExit(f"{label}: free disk under {FLOOR_GB:.0f} GB; stopping before the fetch")
    res = fetch.fetch(url, raw, KEY)
    try:
        q = quarter(zipfile.ZipFile(raw), label)
    except zipfile.BadZipFile:
        raw.unlink()
        return f"- {label}: not a zip ({res.bytes:,} bytes), deleted, no part written"
    PARTS.mkdir(parents=True, exist_ok=True)
    q.to_parquet(part.with_suffix(".tmp"), index=False)
    pd.read_parquet(part.with_suffix(".tmp"), columns=["adsh"])   # the part reads back before the raw file goes
    part.with_suffix(".tmp").replace(part)
    raw.unlink()
    rev = q[q.tag.isin(REVENUE)]
    return (f"- {label}: {res.bytes / 1e6:,.0f} MB fetched and deleted; {len(q):,} facts, {q.adsh.nunique():,} filings, "
            f"{rev.adsh.nunique():,} with revenue by geography")


def ledger_note(n_parts: int) -> None:
    """Record the rehydration in the eviction ledger, so the inventory knows the raw files are gone again by design."""
    path = config.EVICTION_LEDGER_PATH
    led = json.loads(path.read_text(encoding="utf-8"))
    rec = led["sources"][KEY]
    note = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "by": "clean/02zr_fsn_geography.py",
            "purpose": "geographic revenue and assets facts; each quarter fetched, read and deleted (signed off 2026-10-27)",
            "quarters_read": n_parts, "derivative": "20_data/interim/fsn_geo_facts.parquet"}
    rec["rehydrations"] = [r for r in rec.get("rehydrations", []) if r.get("by") != note["by"]] + [note]
    if note["derivative"] not in rec.get("retained_derivatives", []):
        rec.setdefault("retained_derivatives", []).append(note["derivative"])
    led["updated_at"] = note["at"]
    path.write_text(json.dumps(led, indent=1), encoding="utf-8")


def combine() -> None:
    parts = sorted(PARTS.glob("*.parquet"))
    d = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    d.to_parquet(OUT, index=False)
    rev = d[d.tag.isin(REVENUE)]
    say()
    say(f"rows out: {len(d):,} facts from {len(parts)} quarter files; {d.adsh.nunique():,} filings, {d.cik.nunique():,} filers")
    say(f"revenue by geography: {rev.adsh.nunique():,} filings, {rev.cik.nunique():,} filers")
    say()
    say("| axis | facts | filings |")
    say("|:--|--:|--:|")
    for ax, g in d.groupby("axis"):
        say(f"| {ax} | {len(g):,} | {g.adsh.nunique():,} |")
    ledger_note(len(parts))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(LINES) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--combine", action="store_true")
    a = ap.parse_args()
    config.require_contact()
    say("# SEC Financial Statement and Notes: revenue and assets by geography (clean)")
    say()
    if not a.combine:
        todo = [j for j in jobs() if a.only is None or j[1].name.replace("_notes.zip", "") in a.only]
        say(f"rows in: {len(todo)} quarter files")
        for url, raw in todo:
            try:
                say(run_one(url, raw))
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001  one failed quarter must not stop the rest
                say(f"- {raw.name}: FAILED {str(exc)[-160:]}")
    combine()
    return 0


if __name__ == "__main__":
    sys.exit(main())
