"""Check whether each 10-K prints the negative value for the geography the data holds.

Input: CSV from check_negative_rows.py --out (cik, adsh, member, revenue_musd, ...).
For each row, fetch the filing's index and main document from sec.gov, then look for the absolute value, at the
scales a filing may use (millions, thousands, dollars) and at 0 to 3 decimals, written as a negative number:
"(164)", "(164.0)", "-164", "$(164)". Verdict per row:
  printed         a negative form of the value appears in the main document
  not printed     the document was read and only a positive form, or no form, appears
  could not check the filing could not be fetched or read
Nothing in the release data is changed. The contact address comes from PNPANEL_CONTACT in .env and is never
printed. Requests are spaced 0.2 seconds apart (5 per second, under the SEC limit of 10).
"""
import argparse
import html
import json
import os
import re
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
GAP = 0.2


def contact() -> str:
    for p in [ROOT, *ROOT.parents]:
        env = p / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("PNPANEL_CONTACT="):
                    v = line.split("=", 1)[1].strip().strip('"\'')
                    if v:
                        return v
    raise SystemExit("PNPANEL_CONTACT is empty or missing in .env")


UA = None
_last = 0.0


def get(url: str) -> bytes:
    global _last
    wait = GAP - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": f"P18 research {UA}", "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main_docs(cik: int, adsh: str) -> list[str]:
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/"
    idx = json.loads(get(base + "index.json"))
    items = idx["directory"]["item"]
    docs = [i for i in items if i["name"].lower().endswith((".htm", ".html", ".txt"))
            and not re.search(r"(index|-ex|ex\d|R\d+\.htm)", i["name"], re.I)]
    docs.sort(key=lambda i: -int(i.get("size") or 0))
    return [base + i["name"] for i in docs[:2]]


def forms(v: float) -> list[str]:
    """Exact written forms of |v| in millions, thousands and dollars (no rounding).

    A scale is used only if |v| times the scale is a number with at most 3 decimals, so a value such as
    3.9134 musd is searched as 3,913,400 (dollars) and 3,913.4 (thousands), never as "4".
    """
    a = abs(v)
    out = []
    for scale in (1, 1e3, 1e6):
        x = a * scale
        for d in range(4):
            if abs(x - round(x, d)) < 1e-6 * max(1, x):
                out.append(f"{round(x, d):,.{d}f}")
                break
    return list(dict.fromkeys(out))


CTX = re.compile(r"geograph|countr|region|location|foreign|international|intersegment|elimination|revenues? by", re.I)


def verdict(text: str, v: float) -> tuple[str, str]:
    """A negative form counts only if a geography or segment word appears within 600 characters before it."""
    found_pos = None
    for s in forms(v):
        e = re.escape(s)
        neg = rf"\(\s*\$?\s*{e}(?![\d])|(?<![\d.,])[-\u2013\u2014]\s*\$?\s*{e}(?![\d])"
        for m in re.finditer(neg, text):
            if CTX.search(text[max(0, m.start() - 600):m.end()]):
                return "printed", s
        if re.search(rf"(?<![\d.,]){e}(?![\d])", text):
            found_pos = s
    return "not printed", (f"positive form {found_pos} only" if found_pos else "no form of the number found")


def main() -> int:
    global UA
    ap = argparse.ArgumentParser()
    ap.add_argument("rows_csv")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    UA = contact()
    rows = pd.read_csv(a.rows_csv)
    cache, res = {}, []
    for r in rows.itertuples():
        try:
            if r.adsh not in cache:
                txt = ""
                for u in main_docs(int(r.cik), r.adsh):
                    raw = get(u).decode("utf-8", "ignore")
                    txt += " " + html.unescape(re.sub(r"<[^>]+>", " ", raw)).replace("\xa0", " ")
                cache[r.adsh] = re.sub(r"\s+", " ", txt)
            v, note = verdict(cache[r.adsh], r.revenue_musd)
        except Exception as e:  # network error, missing index, blocked
            v, note = "could not check", f"{type(e).__name__}: {str(e)[:80]}"
        res.append(dict(cik=r.cik, company=r.company, adsh=r.adsh, fiscal_year=r.fiscal_year, member=r.member,
                        revenue_musd=r.revenue_musd, verdict=v, note=note))
        print(f"{r.company[:28]:28} {r.fiscal_year} {r.member:14} {r.revenue_musd:>12,.4f}  {v}: {note}")
    out = pd.DataFrame(res)
    out.to_csv(a.out, index=False)
    print("\n" + out.verdict.value_counts().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
