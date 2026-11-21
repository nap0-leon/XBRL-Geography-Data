#!/usr/bin/env python3
"""Print the 10-K passage behind each sampled disagreement between XBRL and Compustat geographic revenue.

WHY. The validation standard reads disagreements in the filing before either side is called wrong. This script
draws a stratified sample of the firm-years `build/03zb_validate_fsn_geography.py` did not score as agreeing,
fetches each 10-K (the largest HTML document of the filing), and prints the text around the first place each
side's numbers appear. The reader records a verdict per firm-year in `config/fsn_geo_adjudication.yaml`.

Outputs
    40_output/tables/fsn_geography_adjudication_sample.md     the passages, one section per firm-year
Usage
    python build/03zc_adjudicate_fsn_geography.py --per-category 6 --seed 11     # passages to read
    python build/03zc_adjudicate_fsn_geography.py --auto 150 --calib 40         # score by printed numbers
    python build/03zc_adjudicate_fsn_geography.py --untested 180 --bench 60     # fiscal 2011-2019 against 2020-2022
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config, fetch  # noqa: E402

I = config.INTERIM
OUT = config.ROOT / "40_output" / "tables" / "fsn_geography_adjudication_sample.md"
CATS = ["other_cut", "relabelled", "partial", "partial_unchecked", "total_only", "differ"]
H = None


def text_of(cik: str, adsh: str) -> str:
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh.replace('-', '')}/"
    items = requests.get(base + "index.json", headers=H, timeout=60).json()["directory"]["item"]
    time.sleep(0.3)
    docs = [x for x in items if x["name"].lower().endswith((".htm", ".html")) and not re.match(r"(?i)^(r\d+|ex|.*ex\d)", x["name"])]
    doc = max(docs, key=lambda x: int(x.get("size") or 0))["name"]
    raw = requests.get(base + doc, headers=H, timeout=120).text
    time.sleep(0.3)
    # The inline XBRL header and hidden blocks repeat every context and number; drop them before searching.
    t = re.sub(r"(?is)<ix:header>.*?</ix:header>", " ", raw)
    t = re.sub(r"(?is)<div[^>]*display:\s*none[^>]*>.*?</div>", " ", t)
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", t)
    t = re.sub(r"(?i)</(td|th)>", " | ", t)
    t = re.sub(r"(?i)</(tr|p|div|br)>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"[ \t\xa0]+", " ", re.sub(r"\n\s*\n+", "\n", t))


def forms(v: float) -> list[str]:
    """How a value in millions may be printed: in millions or thousands, with or without decimals."""
    out = [f"{v:,.1f}", f"{v:,.0f}", f"{v * 1000:,.0f}"] if v >= 1 else [f"{v * 1000:,.0f}", f"{v * 1000:,.1f}"]
    return [f for f in out if len(re.sub(r"\D", "", f)) >= 3]   # a one- or two-digit number matches anywhere


def printed(text: str, val: float) -> bool | None:
    """Whether a value in $m is printed in the filing, as a whole number in millions or thousands or with one
    decimal in millions. None when every form has fewer than three digits (it would match anywhere)."""
    fs = forms(abs(val))
    if not fs:
        return None
    return any(re.search(r"(?<![\d,.])" + re.escape(f) + r"(?![\d,]|\.\d)", text) for f in fs)


def auto(res: pd.DataFrame, xb: pd.DataFrame, cs: pd.DataFrame, n: int, calib: int, seed: int) -> int:
    """Score each sampled firm-year by whether each side's rows are printed in the 10-K. Rows XBRL set aside as
    nested are not scored. A calibration sample of `exact` firm-years measures how often the search misses a number
    that is there (both sides agree, so both should be found)."""
    bad = res[~res.category.isin(["exact", "equivalent", "other_cut", "countries_agree"])]
    smp = pd.concat([bad.sample(min(n, len(bad)), random_state=seed).assign(role="disagreement"),
                     res[res.category == "exact"].sample(min(calib, (res.category == "exact").sum()), random_state=seed).assign(role="calibration")])
    rows = []
    for r in smp.itertuples(index=False):
        x = xb[(xb.gvkey == r.gvkey) & (xb.fyear == r.fyear) & (xb.nested == "") & (xb.kind != "state")]
        c = cs[(cs.gvkey == r.gvkey) & (cs.fyear == r.fyear)]
        try:
            t = text_of(x.cik.iloc[0], x.adsh.iloc[0])
        except Exception as exc:  # noqa: BLE001
            print(f"  {r.gvkey} {r.fyear}: not read ({str(exc)[:60]})", flush=True)
            continue
        px = [printed(t, v) for v in x.revenue_musd]
        pc = [printed(t, v) for v in c.sales]
        fx = [p for p in px if p is not None]
        fc = [p for p in pc if p is not None]
        rows.append((r.role, r.category, r.gvkey, r.fyear, x.adsh.iloc[0], len(fx), sum(fx), len(fc), sum(fc)))
        print(f"  {r.role} {r.category} {r.gvkey} {r.fyear}: XBRL {sum(fx)}/{len(fx)}, Compustat {sum(fc)}/{len(fc)}", flush=True)
    d = pd.DataFrame(rows, columns=["role", "category", "gvkey", "fyear", "adsh", "x_rows", "x_found", "c_rows", "c_found"])
    d["xbrl_printed"] = (d.x_rows > 0) & (d.x_found == d.x_rows)
    d["cs_printed"] = (d.c_rows > 0) & (d.c_found == d.c_rows)
    d.to_parquet(I / "fsn_geo_adjudication_auto.parquet", index=False)
    lines = ["# XBRL against Compustat: which side's numbers the 10-K prints", "",
             f"Sample: {(d.role == 'disagreement').sum()} firm-years that do not agree, and {(d.role == 'calibration').sum()} that agree exactly (calibration), "
             f"tuning years {res.fyear.min()}-{res.fyear.max()}, seed {seed}.", "",
             "| role | category | firm-years | XBRL rows all printed | Compustat rows all printed |", "|:--|:--|--:|--:|--:|"]
    for (role, cat), g in d.groupby(["role", "category"]):
        lines.append(f"| {role} | {cat} | {len(g)} | {g.xbrl_printed.mean():.0%} | {g.cs_printed.mean():.0%} |")
    dis, cal = d[d.role == "disagreement"], d[d.role == "calibration"]
    share_bad = len(bad) / len(res)
    hit = cal.xbrl_printed.mean() if len(cal) else np.nan
    est = (1 - share_bad) + share_bad * (dis.xbrl_printed.mean() / hit if hit else np.nan)
    lines += ["", f"- the search finds every row of an exact firm-year in {hit:.0%} of calibration filings (its hit rate)",
              f"- disagreements are {share_bad:.1%} of tuning firm-years; XBRL's rows are all printed in {dis.xbrl_printed.mean():.0%} of them, Compustat's in {dis.cs_printed.mean():.0%}",
              f"- estimated share of firm-years where XBRL reproduces the filing: {est:.1%} (agreements, plus disagreements where the filing prints XBRL's numbers, corrected for the hit rate)"]
    OUT.with_name("fsn_geography_adjudication_auto.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def untested(xb: pd.DataFrame, n: int, bench: int, seed: int) -> int:
    """Score firm-years that Compustat cannot check (fiscal 2011-2019, no first-reported segments in the
    subscription) by whether every XBRL row is printed in the 10-K. A benchmark sample from fiscal 2020-2022,
    where agreement with Compustat is known, is scored the same way, so the two rates compare like with like."""
    x = xb[(xb.nested == "") & (xb.kind != "state")]
    fy = x.drop_duplicates(["cik", "fyear", "adsh"])[["cik", "fyear", "adsh"]]
    old = fy[fy.fyear.between(2011, 2019)].groupby("fyear", group_keys=False).apply(
        lambda g: g.sample(min(len(g), -(-n // 9)), random_state=seed)).assign(role="untested")
    old["fyear"] = old.adsh.map(fy.set_index("adsh").fyear)   # apply drops the grouping column
    new = fy[fy.fyear.between(2020, 2022)].sample(bench, random_state=seed).assign(role="benchmark")
    rows = []
    for r in pd.concat([old, new]).itertuples(index=False):
        v = x[x.adsh == r.adsh].revenue_musd
        try:
            t = text_of(r.cik, r.adsh)
        except Exception as exc:  # noqa: BLE001
            print(f"  {r.cik} {r.fyear}: not read ({str(exc)[:60]})", flush=True)
            continue
        f = [p for p in (printed(t, val) for val in v) if p is not None]
        rows.append((r.role, r.fyear, r.cik, r.adsh, len(f), sum(f)))
        print(f"  {r.role} {r.fyear} {r.cik}: {sum(f)}/{len(f)}", flush=True)
    d = pd.DataFrame(rows, columns=["role", "fyear", "cik", "adsh", "x_rows", "x_found"])
    d["all_printed"] = (d.x_rows > 0) & (d.x_found == d.x_rows)
    d["row_share"] = d.x_found / d.x_rows.where(d.x_rows > 0)
    d.to_parquet(I / "fsn_geo_adjudication_untested.parquet", index=False)
    return untested_report(d, seed)


def untested_report(d: pd.DataFrame, seed: int) -> int:
    """Write the report of `untested` from its stored scores (`--untested-report` rebuilds it without fetching)."""
    d = d.assign(fyear=d.fyear.astype(int))
    b = d[d.role == "benchmark"]
    lines = ["# XBRL geographic revenue in years Compustat cannot check", "",
             f"Sample: {(d.role == 'untested').sum()} firm-years from fiscal 2011-2019 (stratified by year) and {len(b)} from fiscal 2020-2022 "
             f"(benchmark, where held-out agreement with Compustat is about 90%), seed {seed}. Each firm-year is scored by whether every XBRL row "
             "(nested rows set aside) is printed in the 10-K.", "",
             "| years | firm-years | all rows printed | share of rows printed |", "|:--|--:|--:|--:|"]
    u = d[d.role == "untested"]
    groups = [(str(y), g) for y, g in u.groupby("fyear")]
    groups += [("2011-2014", u[u.fyear <= 2014]), ("2015-2019", u[u.fyear >= 2015]), ("2011-2019", u), ("2020-2022 benchmark", b)]
    for name, g in groups:
        lines.append(f"| {name} | {len(g)} | {g.all_printed.mean():.0%} | {g.row_share.mean():.0%} |")
    p, q = u.all_printed.mean(), b.all_printed.mean()
    se = np.sqrt(p * (1 - p) / len(u) + q * (1 - q) / len(b))
    lines += ["", f"- relative fidelity of 2011-2019 to the benchmark: {p / q:.2f} "
              "(1.00 means the untested years reproduce the filings as often as the validated years)",
              f"- difference {p - q:+.1%}, standard error {se:.1%}; per-year rows hold about 20 firm-years each and are noisy"]
    OUT.with_name("fsn_geography_untested_years.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def main() -> int:
    global H
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-category", type=int, default=6)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--auto", type=int, default=0, help="score this many disagreements by printed numbers, no passages")
    ap.add_argument("--calib", type=int, default=40)
    ap.add_argument("--untested", type=int, default=0, help="score this many fiscal 2011-2019 firm-years by printed numbers")
    ap.add_argument("--bench", type=int, default=60)
    ap.add_argument("--untested-report", action="store_true", help="rebuild the --untested report from stored scores")
    a = ap.parse_args()
    H = fetch.headers_for("edgar_fts")
    if a.untested:
        return untested(pd.read_parquet(I / "fsn_geo_revenue.parquet"), a.untested, a.bench, a.seed)
    if a.untested_report:
        return untested_report(pd.read_parquet(I / "fsn_geo_adjudication_untested.parquet"), a.seed)
    s = importlib.util.spec_from_file_location("v", Path(__file__).with_name("03zb_validate_fsn_geography.py"))
    v = importlib.util.module_from_spec(s)
    s.loader.exec_module(v)
    res = pd.read_parquet(I / "fsn_geo_validation.parquet")
    res = res[res.fyear.between(*v.TUNE)]   # never read the held-out years while the rules are being tuned
    xb = pd.read_parquet(I / "fsn_geo_revenue.parquet")
    cs = v.compustat()
    if a.auto:
        return auto(res, xb, cs, a.auto, a.calib, a.seed)
    smp = pd.concat([g.sample(min(a.per_category, len(g)), random_state=a.seed) for c, g in res.groupby("category") if c in CATS])
    lines = ["# XBRL against Compustat geographic revenue: passages for adjudication", ""]
    for r in smp.itertuples(index=False):
        x = xb[(xb.gvkey == r.gvkey) & (xb.fyear == r.fyear)]
        c = cs[(cs.gvkey == r.gvkey) & (cs.fyear == r.fyear)]
        cik, adsh = x.cik.iloc[0], x.adsh.iloc[0]
        lines += [f"## {r.category}: gvkey {r.gvkey}, fiscal {r.fyear}, CIK {cik}, {adsh}", "",
                  "XBRL: " + "; ".join(f"{m} ({k}{', ' + n if n else ''}) {val:,.1f}" for m, k, n, val in zip(x.member, x.kind, x.nested, x.revenue_musd)),
                  "Compustat: " + "; ".join(f"{gg} {val:,.1f}" for gg, val in zip(c.gareag.fillna("?"), c.sales)), f"Sales (funda): {r.sale:,.1f}", ""]
        try:
            t = text_of(cik, adsh)
        except Exception as exc:  # noqa: BLE001
            lines += [f"(filing not read: {str(exc)[:120]})", ""]
            continue
        shown = []
        for val in list(x.revenue_musd) + list(c.sales):
            for f in forms(abs(val)):
                k = t.find(f)
                if k >= 0 and not any(abs(k - j) < 1500 for j in shown):
                    shown.append(k)
                    break
        for k in sorted(shown)[:3]:
            lines += ["```", t[max(0, k - 900):k + 600].strip(), "```", ""]
        print(f"{r.category} {r.gvkey} {r.fyear}: {len(shown)} passages", flush=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
