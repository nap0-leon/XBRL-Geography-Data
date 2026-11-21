"""Screen for XBRL facts that are year-on-year changes but sit on the geography axis as if they were revenue levels.

Why this population. A change fact used as a level breaks the filing's sum to total revenue, so a material one can only
sit in a filing that does not sum within 1% (`sums_to_filing_revenue == False`). Filings that sum are not read.

Method. For each sampled filing, fetch the main 10-K document and, for every used row, search for its exact written
forms (millions, thousands, dollars; see check_negative_printed.forms). A row is
  level_evidence   some occurrence has no change wording within 120 characters (a table cell or a plain statement)
  change_only      it occurs, but only next to words such as increase, decrease, declined, compared
  not_found        no exact form occurs (rounded tables, other units, or the value is not printed)
Rows with fewer than 4 digits in their written form are skipped, because short numbers match unrelated text.
A filing is flagged if it has a change_only row. Flags are leads, not proof: read them.

Usage: python code/tests/check_change_facts.py --n 300 --seed 7 --out rows.csv
Requests are spaced 0.2 seconds apart; the contact address comes from PNPANEL_CONTACT in .env.
"""
import argparse
import html
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_negative_printed as c  # noqa: E402

ROOT = c.ROOT
CHANGE = re.compile(r"increas|decreas|declin|grew|growth|higher|lower|compared|chang|rose|fell|reduc|down|up \$", re.I)


def classify(text: str, v: float) -> str:
    fs = [s for s in c.forms(v) if sum(ch.isdigit() for ch in s) >= 4]
    hits = []
    for s in fs:
        hits += [m for m in re.finditer(rf"(?<![\d.,]){re.escape(s)}(?![\d])", text)]
    if not hits:
        return "not_found"
    for m in hits:
        if not CHANGE.search(text[max(0, m.start() - 120):m.end() + 120]):
            return "level_evidence"
    return "change_only"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pool", choices=["nonsumming", "nototal"], default="nonsumming",
                    help="nonsumming: has a total and the used rows miss it by more than 1 percent; nototal: no reference total")
    ap.add_argument("--exclude", help="CSV from an earlier run; its filings are skipped")
    a = ap.parse_args()
    c.UA = c.contact()
    geo = pd.read_parquet(ROOT / "data" / "geo_revenue.parquet")
    fs = pd.read_parquet(ROOT / "data" / "filing_summary.parquet")
    if a.pool == "nototal":
        pool = fs[fs.coverage.isna() & (fs.rows_used > 1)]
    else:
        pool = fs[fs.coverage.notna() & ~fs.sums_to_filing_revenue & (fs.rows_used > 1)]
    print(f"pool '{a.pool}': {len(pool):,} filings")
    if a.exclude:
        pool = pool[~pool.adsh.isin(pd.read_csv(a.exclude).adsh)]
        print(f"after excluding filings already read: {len(pool):,}")
    samp = pool.sample(min(a.n, len(pool)), random_state=a.seed)
    used = geo[(geo.nested == "") & (geo.kind != "state")]
    out = []
    for i, f in enumerate(samp.itertuples(), 1):
        try:
            txt = ""
            for u in c.main_docs(int(f.cik), f.adsh):
                raw = c.get(u).decode("utf-8", "ignore")
                txt += " " + html.unescape(re.sub(r"<[^>]+>", " ", raw)).replace("\xa0", " ")
            txt = re.sub(r"\s+", " ", txt)
        except Exception as e:
            out.append(dict(adsh=f.adsh, company=f.company, fiscal_year=f.fiscal_year, coverage=f.coverage, member="",
                            kind="", revenue_musd=float("nan"), verdict="could_not_fetch", note=type(e).__name__))
            continue
        for r in used[used.adsh == f.adsh].itertuples():
            out.append(dict(adsh=f.adsh, company=f.company, fiscal_year=f.fiscal_year, coverage=f.coverage,
                            member=r.member, kind=r.kind, revenue_musd=r.revenue_musd,
                            verdict=classify(txt, r.revenue_musd), note=""))
        if i % 25 == 0:
            print(f"  {i}/{len(samp)}", flush=True)
    d = pd.DataFrame(out)
    d.to_csv(a.out, index=False)
    ok = d[d.verdict != "could_not_fetch"]
    print("\nfilings fetched:", ok.adsh.nunique(), "could not fetch:", d[d.verdict == 'could_not_fetch'].adsh.nunique())
    print(ok.verdict.value_counts().to_string())
    flagged = ok[ok.verdict == "change_only"]
    print(f"\nfilings with a change_only row: {flagged.adsh.nunique()} of {ok.adsh.nunique()}")
    with pd.option_context("display.width", 220, "display.max_colwidth", 36):
        print(flagged[["company", "fiscal_year", "member", "kind", "revenue_musd", "coverage"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
