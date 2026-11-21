#!/usr/bin/env python3
"""Read XBRL revenue by geography into countries and regions, and validate it against Compustat geographic segments.

STANDARD. 95% agreement with Compustat, which is not ground truth; disagreements are read in the filing before
either side is called wrong (as in `build/03y_validate_segments.py`). Agreement is scored at the resolution both
sides report: a firm-year where one side names countries and the other a region that holds them agrees when the
domestic and the total revenue agree.

MEMBERS. The notes files drop the namespace, so `CA` is Canada (country:CA) or California (stpr:CA). A table
(filing x axis) is read as states when it holds more codes that are only states (TX, FL, NY) than codes that are
only countries (GB, CN, JP); otherwise as countries. Each member is then one of
    domestic        US, UnitedStates, Domestic, CountryOfDomicile, GeographicDistributionDomestic
    foreign_total   NonUs, Foreign, International, OutsideofParentCountry, ForeignCountries, NonUnitedStates
    country         an ISO2 code in a country table, or a country name (`China`, `UnitedKingdom`)
    state           a state code in a state table
    total           a member whose words say total or consolidated (dropped: it double counts)
    region          everything else (Europe, AsiaPacific, EMEA, RestOfWorld, OtherCountries)

FACTS USED. Annual revenue, as first reported (fact date = the filing's period end), facts with no other axis.
One tag per filing: external revenue when tagged, otherwise the revenue tag whose geographic sum is closest to
Compustat sales. One axis per filing: the one with the most facts.

A firm-year (Compustat first-reported GEOSEG, `srcdate` = `datadate`) is
    exact        domestic within 2% (or absent on both sides), and every named foreign country of either side
                 is named by the other within 2%
    equivalent   not exact, but domestic and total within 2%: the same numbers cut at different resolutions
    differ       otherwise

Categories `partial` (XBRL tags under 98% of sales, every row consistent with Compustat) and `total_only` (totals
agree, cuts do not nest) are reported apart: neither is a wrong number, neither is a full check.

Outputs
    20_data/interim/fsn_geo_revenue.parquet          gvkey, cik, adsh, fyear, member, kind, iso3, nested, revenue_musd
    20_data/interim/fsn_geo_validation.parquet       gvkey, fyear, category, domestic and total on each side
    40_output/tables/fsn_geography_validation.md
    20_data/interim/fsn_geo_revenue_public.parquet   with --public: the same table built with no Compustat input
Usage
    python build/03zb_validate_fsn_geography.py
    python build/03zb_validate_fsn_geography.py --public
"""

from __future__ import annotations

import itertools
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import config, geo, regions  # noqa: E402

I, W = config.INTERIM, config.RAW / "wrds"
REPORT = config.ROOT / "40_output" / "tables" / "fsn_geography_validation.md"
EXTERNAL = ["SegmentReportingInformationRevenueFromExternalCustomers", "RevenueFromExternalCustomers"]
REVENUE = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
           "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet", "SalesRevenueServicesNet",
           "RevenuesNetOfInterestExpense", "OperatingRevenue", "RevenuesExcludingInterestAndDividends"]
# Codes BACI does not carry as their own reporter.
ISO_EXTRA = {"TW": "TWN", "PR": "PRI", "XK": "XKX", "GU": "GUM", "VI": "VIR", "AS": "ASM", "MP": "MNP"}
DOMESTIC = re.compile(r"(?i)^(us|usa|united ?states( of america)?|domestic( plan| operations| sales| revenue)?|country of domicile|"
                      r"geographic distribution domestic|u ?s|north america us|domestic destination)$")
FOREIGN = re.compile(r"(?i)^(non ?us|non ?u ?s|foreign|international|outside ?of ?parent ?country|foreign ?countries|"
                     r"non ?united ?states|countries ?outside ?united ?states|outside ?(the )?(us|united ?states)|"
                     r"geographic distribution foreign|all ?foreign ?countries|other ?foreign ?countries)$")
TOTAL = re.compile(r"(?i)\b(total|consolidated)\b")
US_PREFIX = re.compile(r"(?i)^(u\.? ?s\.?|united states)\b")   # "U.S. Gulf Of Mexico", "United States Operations"
NOT_US = re.compile(r"(?i)(\b(non|outside|excluding|except|and|other|plus|international)\b|canada|mexico|puerto)")
TOL = 0.02
UNION_TAGS = True   # take members of every revenue tag (see `xbrl`)
# Rules are tuned on these fiscal years only. Later years are a held-out test: read their agreement, do not tune on it.
# FROZEN 2026-10-27 after tuning on 3,191 firm-years (fiscal 2020-2022): 89.3% agree; the 10-K prints XBRL's rows in
# 75% of the rest and Compustat's in 64% (search hit rate 88%), so XBRL reproduces the filing in about 98% of
# firm-years (40_output/tables/fsn_geography_adjudication_auto.md). Change a rule after this line only with a new
# tuning round and a new held-out year.
# 2026-11-21: the reference total each filing is compared with now comes from `build/03zbb_fsn_geography_reference.py`
# (the filing's own revenue fact in SEC companyfacts) and not from `sec_firm_year` with a fallback to another filing. This
# is an input, not a rule: the classification and nesting rules above are unchanged.
# 2026-10-29: the tag choice got a stable tie-break (gap, then tag order) so reruns and the Compustat-free --public
# build pick the same tag. 10 of 9,407 firm-years changed category; held-out agreement stays 90.7% (differ 1.0% to
# 0.9%). Fiscal 2011-2019, which Compustat cannot check, reproduce the 10-K about as often as fiscal 2020-2022
# (86% against 88% of sampled filings, standard error 4.9 points; 40_output/tables/fsn_geography_untested_years.md).
# HELD-OUT, run once after the freeze with every quarter read: fiscal 2023-2026, 4,706 firm-years, 90.7% agree, 1.0%
# differ (tuning years with every quarter read: 4,701 firm-years, 89.8%). Log: 40_output/logs/fsn_geo_heldout_20261027.log
TUNE = (2020, 2022)
SHOW_TEST = "--show-test" in sys.argv
LINES: list[str] = []


def say(s: str = "") -> None:
    print(s, flush=True)
    LINES.append(s)


def iso_table() -> tuple[dict, dict]:
    z = zipfile.ZipFile(config.RAW / "cepii_baci" / "BACI_HS92_V202601.zip")
    c = pd.read_csv(next(z.open(n) for n in z.namelist() if n.startswith("country_codes")), dtype=str, keep_default_na=False)
    c = c[c.country_iso2.str.fullmatch(r"[A-Z]{2}")]
    iso = dict(zip(c.country_iso2, c.country_iso3)) | ISO_EXTRA
    names = {re.sub(r"[^a-z]", "", n.lower()): i3 for n, i3 in zip(c.country_name, c.country_iso3)}
    names |= {"unitedkingdom": "GBR", "uk": "GBR", "china": "CHN", "prc": "CHN", "peoplesrepublicofchina": "CHN", "southkorea": "KOR",
              "korea": "KOR", "taiwan": "TWN", "hongkong": "HKG", "russia": "RUS", "vietnam": "VNM", "netherlands": "NLD",
              "switzerland": "CHE", "unitedarabemirates": "ARE", "czechrepublic": "CZE", "puertorico": "PRI", "germany": "DEU"}
    return iso, names


def classify(g: pd.DataFrame, iso: dict, names: dict) -> pd.DataFrame:
    states = set(geo.STATE_FIPS.values())
    codes = set(g.member)
    state_table = len(codes & (states - set(iso))) > len(codes & (set(iso) - states))
    kind, i3 = [], []
    for m, w in zip(g.member, g.member_words):
        key = re.sub(r"[^a-z]", "", w.lower())
        if TOTAL.search(w):
            kind.append("total"); i3.append(None)
        elif US_PREFIX.match(w) and not NOT_US.search(w):
            kind.append("domestic"); i3.append("USA")
        elif DOMESTIC.match(w) or m in ("US", "USA"):
            kind.append("domestic"); i3.append("USA")
        elif FOREIGN.match(w):
            kind.append("foreign_total"); i3.append(None)
        elif m in states and state_table:
            kind.append("state"); i3.append(None)
        elif m in iso:
            kind.append("country"); i3.append(iso[m])
        elif key in names:
            kind.append("country"); i3.append(names[key])
        else:
            kind.append("region"); i3.append(None)
    return g.assign(kind=kind, iso3=i3, state_table=state_table)


def funda_sale() -> pd.Series:
    fa = pd.read_parquet(W / "funda.parquet", columns=["gvkey", "fyear", "sale", "indfmt", "consol", "datafmt", "popsrc"])
    fa = fa[(fa.indfmt == "INDL") & (fa.consol == "C") & (fa.datafmt == "STD") & (fa.popsrc == "D")].dropna(subset=["fyear"])
    fa["sale"] = pd.to_numeric(fa.sale, errors="coerce")
    s = fa.assign(fyear=fa.fyear.astype(int)).set_index(["gvkey", "fyear"]).sale
    return s[~s.index.duplicated()]


def filing_revenue() -> tuple[dict, dict]:
    """The filing's own total revenue in $m, by accession (and an empty mapping by CIK and fiscal year).

    It is the table's own total, which Compustat sales may not be (Valero 2020: $64,912m with excise taxes against
    sales of $60,115m; Bank of America: revenue net of interest expense). `build/03zbb_fsn_geography_reference.py`
    finds it in SEC companyfacts by accession number. Until 2026-11-21 this read `sec_firm_year` and fell back to the
    value of another filing of the same CIK and fiscal year; that returned an 8-K with recast figures (Visteon 2014), the
    prior year (Karbon-X 2025, Mantra Venture 2016) or a different fact (EACO 2012). There is no fallback now: a filing
    without a revenue fact of its own has no reference."""
    t = pd.read_parquet(I / "fsn_geo_reference_totals.parquet", columns=["adsh", "ref_musd"]).dropna(subset=["ref_musd"])
    return dict(zip(t.adsh, t.ref_musd)), {}


def xbrl(sale: pd.Series) -> pd.DataFrame:
    f = pd.read_parquet(I / "fsn_geo_facts.parquet")
    f = f[f.tag.isin(EXTERNAL + REVENUE) & (f.qtrs == "4") & (f.other_axes == "") & (f.ddate == f.period)].copy()
    f = f[f.value.notna() & (f.value != 0)]
    say(f"- XBRL revenue facts by geography, as first reported, no other axis: {len(f):,} in {f.adsh.nunique():,} filings")
    top = f.groupby(["adsh", "axis"]).size().reset_index(name="n").sort_values(["adsh", "n"]).drop_duplicates("adsh", keep="last")
    f = f.merge(top[["adsh", "axis"]], on=["adsh", "axis"])
    x = pd.read_parquet(I / "firm_xref_cik_gvkey.parquet")
    x["cik"] = x.cik.astype(str).str.lstrip("0")
    f["cik"] = f.cik.astype(str).str.lstrip("0")
    f = f.merge(x[["cik", "gvkey"]].drop_duplicates("cik"), on="cik", how="left")
    say(f"- filings linked to a GVKEY: {f[f.gvkey.notna()].adsh.nunique():,} of {f.adsh.nunique():,}")
    f["gvkey"] = f.gvkey.astype("string").str.zfill(6)
    dd = pd.to_datetime(f.ddate, format="%Y%m%d", errors="coerce")
    f["fyear"] = np.where(dd.dt.month <= 5, dd.dt.year - 1, dd.dt.year)
    iso, names = iso_table()
    f = pd.concat([classify(g, iso, names) for _, g in f.groupby("adsh")], ignore_index=True)
    f = f[f.kind != "total"]
    ext = set(f[f.tag.isin(EXTERNAL)].adsh)
    f = f[(f.tag.isin(EXTERNAL) & f.adsh.isin(ext)) | (f.tag.isin(REVENUE) & ~f.adsh.isin(ext))]
    by_adsh, by_cik = filing_revenue()
    ref = {}
    for a, c, y, g in f[["adsh", "cik", "fyear", "gvkey"]].drop_duplicates("adsh").itertuples(index=False):
        r = by_adsh.get(a, by_cik.get((str(c), int(y)), np.nan))
        ref[a] = r if pd.notna(r) and r > 0 else (sale.get((g, int(y)), np.nan) if pd.notna(g) else np.nan)
    f["ref_musd"] = f.adsh.map(ref)
    n_cik = sum(1 for a, c, y in f[["adsh", "cik", "fyear"]].drop_duplicates("adsh").itertuples(index=False)
                if a not in by_adsh and (str(c), int(y)) in by_cik)
    say(f"- reference total: the filing's own revenue for {sum(1 for a in ref if a in by_adsh):,} filings by accession and "
        f"{n_cik:,} by CIK and fiscal year; Compustat sales for the rest of {len(ref):,}")
    tot = f[f.kind != "state"].groupby(["adsh", "gvkey", "fyear", "tag"], dropna=False).value.sum().reset_index()
    tot["sale"] = tot.adsh.map(ref)
    tot["gap"] = (tot.value / 1e6 / tot.sale - 1).abs()
    # Ties and filings with no reference total (gap missing) fall to the order of EXTERNAL + REVENUE, with a
    # stable sort, so a rerun picks the same tag.
    order = {t: k + 1 for k, t in enumerate(EXTERNAL + REVENUE)}
    tot["prio"] = tot.tag.map(order)
    tot = tot.sort_values(["adsh", "gap", "prio"], kind="mergesort").drop_duplicates("adsh")
    # Members of every revenue tag, one value per member: the tag closest to sales first, then the others in the
    # order of REVENUE. A filer may tag its regions under one revenue tag and its material countries under another
    # (IBM 2020: regions, and the US and Japan); `nesting` then sets aside what double counts.
    best = tot.set_index("adsh").tag
    f["prio"] = [0 if t == best.get(a) else order[t] for a, t in zip(f.adsh, f.tag)]
    if not UNION_TAGS:
        f = f[f.prio == 0]
    f = f.sort_values(["adsh", "member", "prio"], kind="mergesort").drop_duplicates(["adsh", "member"])
    f = f.sort_values("adsh", kind="mergesort").drop_duplicates(["cik", "fyear", "member"], keep="last")
    f = f.assign(revenue_musd=f.value / 1e6)
    f = pd.concat([nesting(g, g.ref_musd.iloc[0]) for _, g in f.groupby("adsh")], ignore_index=True)
    return f[["gvkey", "cik", "adsh", "fyear", "axis", "member", "member_words", "kind", "iso3", "state_table", "nested", "revenue_musd", "ref_musd"]]


def _subset(target: float, vals: list[tuple[int, float]], tol: float, max_k: int = 6) -> tuple[int, ...] | None:
    """Indices of 1 to max_k values that sum to target within tol (relative), smallest subset first; or all the
    values, however many, when together they make the target (Compustat OTHER = eight XBRL countries)."""
    if len(vals) > max_k and abs(sum(v for _, v in vals) - target) <= tol * abs(target):
        return tuple(i for i, _ in vals)
    for k in range(1, min(max_k, len(vals)) + 1):
        for comb in itertools.combinations(vals, k):
            if abs(sum(v for _, v in comb) - target) <= tol * abs(target):
                return tuple(i for i, _ in comb)
    return None


RESIDUAL = re.compile(r"(?i)\b(other|rest of|remaining|all other|unallocated|corporate|eliminat)")


def _holds(row) -> frozenset | None:
    """The countries a member holds: its own country, or what its name holds. A residual (OtherCountries,
    RestOfWorld) holds the countries not listed, which no rule can know, so it holds None here."""
    if row.kind in ("country", "domestic") and isinstance(row.iso3, str):
        return frozenset([row.iso3])
    if RESIDUAL.search(row.member_words):
        return None
    return regions.holds(row.member)


def _unit(g: pd.DataFrame) -> float:
    """The rounding unit of the filing's table, in millions: the largest of $1m, $100k, $1k, $1 that divides every
    value (the facts are stored in dollars as printed)."""
    vals = (g.revenue_musd * 1e6).round().abs().astype("int64")
    for u in (1_000_000, 100_000, 1_000, 1):
        if (vals % u == 0).all():
            return u / 1e6
    return 1e-6


def _parts(i: int, g: pd.DataFrame, live: set, max_k: int, unit: float) -> tuple[int, ...] | None:
    """Two or more live members, smaller than member i, whose sum is member i to the rounding of the table: half a
    unit per printed figure. Member i must hold known countries, and its parts must lie inside it; at most one part
    may be of unknown extent (the table's "other" line). A domestic member may hold regions of unknown extent (US
    markets, US cities)."""
    v = g.at[i, "revenue_musd"]
    outer = _holds(g.loc[i])
    dom = g.at[i, "kind"] == "domestic"
    if outer is None:
        return None
    pool = []
    for j in live:
        if j == i or not (0 < g.at[j, "revenue_musd"] < v):
            continue
        inner = _holds(g.loc[j])
        if inner is not None and not inner <= outer:
            continue
        if dom and g.at[j, "kind"] != "region":
            continue
        pool.append((j, g.at[j, "revenue_musd"], inner is None))
    for k in range(2, min(max_k, len(pool)) + 1):
        for comb in itertools.combinations(pool, k):
            if not dom and sum(u for _, _, u in comb) > 1:
                continue
            if abs(sum(x for _, x, _ in comb) - v) <= 0.5 * (k + 1) * unit + 1e-9:
                return tuple(j for j, _, _ in comb)
    return None


def nesting(g: pd.DataFrame, sale: float) -> pd.DataFrame:
    """Mark members that a filing lists next to their parts, which double count when summed.

    0. Duplicates: two members of the same value (to the table's rounding) that hold the same countries, or two
       totals of the rest of the world (International and NonUs at $1,420.7m), keep one; the other is `duplicate`.
    1. Exact sums, no outside number. A member equal to the sum of two or more smaller members that it holds
       (`core.regions`) is a `subtotal`: NonUs next to Asia, EMEA and LatinAmerica (Bank of America 2020); Americas
       next to US, Canada and LatinAmerica (IDEXX 2020). XBRL values are the filing's own figures, so a true
       subtotal matches to the rounding of the table. Regions of unknown extent that together equal the domestic
       member are a breakdown of the US (Host Hotels 2020: 23 markets) and are marked `part_of_domestic`.
    2. The sales gap. When what is left still sums to more than Compustat sales by over 2%, a set of one to four
       members that sums to the excess (within 1% of sales) is set aside. A member may enter the set only if the
       table gives a reason it double counts: it holds another listed member (a parent whose parts do not sum
       exactly, as NonUs net of eliminations), it lies inside a larger listed member or a larger residual line (a
       child: Germany inside Europe, China next to RestOfWorld), or its extent is unknown (a customer name on the
       geographic axis). A member with none of these is never set aside: sales may differ from the table's total
       for reasons of its own (Valero 2020: excise taxes). Sets are ranked by fit (to 0.25% of sales), then by cost
       (a region or parent 0; a country, the domestic member or an unknown member 1), then by size.

    Every row stays, with `nested` set, so the country detail is not lost."""
    g = g.assign(nested="")
    if len(g) > 40:
        return g
    unit = _unit(g)
    live = set(g.index)
    H = {i: _holds(g.loc[i]) for i in g.index}
    for i in g.index:                                        # 0. duplicates
        for j in g.index:
            if j <= i or i not in live or j not in live or g.at[i, "revenue_musd"] <= 0:
                continue
            if abs(g.at[i, "revenue_musd"] - g.at[j, "revenue_musd"]) > unit + 1e-9:
                continue
            rest = [k for k in (i, j) if g.at[k, "kind"] == "foreign_total" or RESIDUAL.search(g.at[k, "member_words"])]
            if (H[i] is not None and H[i] == H[j]) or len(rest) == 2:
                drop = j
            elif H[i] is not None and H[j] is not None and H[i] < H[j]:
                drop = j                                     # NorthAmerica = US to the dollar: keep US
            elif H[i] is not None and H[j] is not None and H[j] < H[i]:
                drop = i
            elif len(rest) == 1 and (H[i] is None or H[j] is None):
                drop = rest[0]                               # NonUs = RestOfWorld: one line, two names
            else:
                continue
            g.at[drop, "nested"] = "duplicate"
            live.discard(drop)
    dom = [i for i in live if g.at[i, "kind"] == "domestic"]
    unk = [i for i in live if g.at[i, "kind"] == "region" and H[i] is None and not RESIDUAL.search(g.at[i, "member_words"])]
    if len(dom) == 1 and len(unk) >= 2:                      # 1a. a breakdown of the US
        d = g.at[dom[0], "revenue_musd"]
        s = g.loc[unk, "revenue_musd"].sum()
        others = [i for i in live if g.at[i, "kind"] == "region" and RESIDUAL.search(g.at[i, "member_words"]) and re.search(r"(?i)\bu ?s\b|united states", g.at[i, "member_words"])]
        s2 = s + g.loc[others, "revenue_musd"].sum()
        for parts, tot in ((unk, s), (unk + others, s2)):
            if abs(tot - d) <= 0.01 * d:
                g.loc[parts, "nested"] = "part_of_domestic"
                live -= set(parts)
                break
    max_k = 8 if len(live) <= 16 else 4
    for i in sorted(live, key=lambda i: -abs(g.at[i, "revenue_musd"])):   # 1b. exact subtotals
        if i not in live or g.at[i, "kind"] == "state" or g.at[i, "revenue_musd"] <= 0:
            continue
        hit = _parts(i, g, live, max_k, unit)
        if hit is None:
            continue
        if g.at[i, "kind"] == "domestic" and all(g.at[j, "kind"] == "region" for j in hit):
            g.loc[list(hit), "nested"] = "part_of_domestic"
            live -= set(hit)
        else:
            g.at[i, "nested"] = "subtotal"
            live.discard(i)
    if pd.isna(sale) or sale <= 0:
        return g
    excess = g.loc[list(live), "revenue_musd"].sum() - sale
    if excess <= TOL * sale:
        return g
    val = {i: g.at[i, "revenue_musd"] for i in live}
    resid = [i for i in live if RESIDUAL.search(g.at[i, "member_words"]) or g.at[i, "kind"] == "foreign_total"]
    cand = []
    for i in live:                                           # 2. the sales gap
        if g.at[i, "kind"] == "state" or val[i] <= 0:
            continue
        mine = H[i]
        parent = mine is not None and any(j != i and H[j] is not None and H[j] < mine for j in live)
        child = mine is not None and (any(j != i and H[j] is not None and mine < H[j] and val[j] > val[i] for j in live)
                                      or any(j != i and val[j] > val[i] for j in resid))
        unknown = mine is None and g.at[i, "kind"] in ("region", "foreign_total")
        if parent or child or unknown:
            cost = 0 if (g.at[i, "kind"] in ("region", "foreign_total") and not unknown) else 1
            cand.append((i, val[i], cost))
    best = None
    for k in range(1, min(4 if len(cand) <= 12 else 3, len(cand)) + 1):
        for comb in itertools.combinations(cand, k):
            gap = abs(sum(v for _, v, _ in comb) - excess)
            if gap <= 0.01 * sale:
                key = (round(gap / (0.0025 * sale)), sum(c for _, _, c in comb), k)
                if best is None or key < best[0]:
                    best = (key, [i for i, _, _ in comb])
    if best is not None:
        for i in best[1]:
            g.at[i, "nested"] = "parent_by_sales" if (H[i] is not None and any(j != i and H[j] is not None and H[j] < H[i] for j in live)) else "part_of_listed_parent"
    return g


def compustat() -> pd.DataFrame:
    s = pd.read_parquet(W / "seg_business.parquet", columns=["gvkey", "stype", "sid", "sales", "datadate", "srcdate"])
    s = s[s.stype == "GEOSEG"].copy()
    s["sales"] = pd.to_numeric(s.sales, errors="coerce")
    s = s[(pd.to_datetime(s.srcdate) == pd.to_datetime(s.datadate)) & (s.sales > 0)]
    g = pd.read_parquet(W / "seg_geo.parquet").query("stype == 'GEOSEG'").drop(columns="stype").drop_duplicates(["gvkey", "sid", "datadate"])
    s = s.merge(g, on=["gvkey", "sid", "datadate"], how="left")
    dd = pd.to_datetime(s.datadate)
    s["fyear"] = np.where(dd.dt.month <= 5, dd.dt.year - 1, dd.dt.year)
    return s[["gvkey", "fyear", "sid", "gareat", "gareag", "sales"]]


def near(a: float, b: float) -> bool:
    return abs(a - b) <= TOL * max(abs(b), 1e-9)


def _partition(coarse: list[tuple[str | None, float]], fine: list[tuple[str | None, float]], labels: bool = True) -> bool:
    """Every coarse row is the sum of a disjoint set of fine rows, and every fine row is used. With `labels`, a
    coarse row that names a country takes only fine rows of the same country."""
    left = list(enumerate(fine))
    total = sum(abs(v) for _, v in fine) or 1.0
    for lab, v in sorted(coarse, key=lambda r: -abs(r[1])):
        pool = [(i, fv) for i, (fl, fv) in left if not labels or lab is None or fl == lab]
        hit = _subset(v, pool, TOL)
        if hit is None:
            if abs(v) <= TOL * total:      # a coarse row under 2% of the total may have no counterpart
                continue
            return False
        left = [(i, r) for i, r in left if i not in hit]
    return sum(abs(r[1]) for _, r in left) <= TOL * total   # fine rows left over hold under 2% of the total


def _covers(coarse: list[tuple[str | None, float]], rows: list[tuple[str | None, float]]) -> bool:
    """Every Compustat row is a disjoint sum of XBRL rows, set-aside rows included: the filing holds both cuts and
    Compustat took the other one (Americas, AsiaPacific, EMEA where the rule kept US, China, RestOfWorld)."""
    left = list(enumerate(rows))
    for lab, v in sorted(coarse, key=lambda r: -abs(r[1])):
        pool = [(i, fv) for i, (fl, fv) in left if lab is None or fl is None or fl == lab]
        hit = _subset(v, pool, TOL)
        if hit is None:
            return False
        left = [(i, r) for i, r in left if i not in hit]
    return True


ALLX: dict = {}


def _countries_agree(coarse: list[tuple[str | None, float]], rows: list[tuple[str | None, float]]) -> bool:
    """Every country Compustat names, the US included, is an XBRL row of the same country within 2%, and Compustat
    names at least one: only the residual differs (H.B. Fuller 2020: Compustat OTHER is each region less its listed
    country)."""
    named = [(lab, v) for lab, v in coarse if lab is not None]
    return bool(named) and all(any(fl == lab and near(fv, v) for fl, fv in rows) for lab, v in named)


def compare(xb: pd.DataFrame, cs: pd.DataFrame, sale: pd.Series) -> pd.DataFrame:
    """Categories, best first:
        exact         the same rows: one-to-one within 2%, and a country on both sides is the same country
        equivalent    the same numbers at different resolutions: one side's rows sum to each of the other side's rows
        relabelled    the numbers nest only if a country label on one side is read as a region on the other
                      (Compustat USA = XBRL Americas): same numbers, a label to read in the filing
        other_cut     the filing holds two cuts and Compustat took the one the rule set aside: every Compustat row
                      is a disjoint sum of XBRL rows, set-aside rows included
        countries_agree  totals agree and every country Compustat names matches XBRL; only the residual differs
        partial       XBRL tags under 98% of sales, and each XBRL row matches a Compustat row or a sum of them
        partial_unchecked  XBRL tags under 98% of sales, and its rows cannot be matched to Compustat's cut
        total_only    the totals agree but the cuts do not nest (US and Other against Americas and Other)
        differ        otherwise"""
    rows = []
    cg = {k: g for k, g in cs.groupby(["gvkey", "fyear"])}
    allx = xb[xb.gvkey.notna() & (xb.kind != "state") & (xb.nested != "duplicate")]
    ALLX.clear()
    ALLX.update({(gv, int(fy)): [(i if k in ("country", "domestic") else None, v) for k, i, v in zip(g.kind, g.iso3, g.revenue_musd)]
                 for (gv, fy), g in allx.groupby(["gvkey", "fyear"])})
    use = xb[xb.gvkey.notna() & (xb.kind != "state") & (xb.nested == "")]
    for (gv, fy), g in use.groupby(["gvkey", "fyear"]):
        c = cg.get((gv, int(fy)))
        if c is None:
            continue
        X = [(i if k in ("country", "domestic") else None, v) for k, i, v in zip(g.kind, g.iso3, g.revenue_musd)]
        C = [(a if t == "ISO" else None, v) for t, a, v in zip(c.gareat, c.gareag, c.sales)]
        tx, tc = sum(v for _, v in X), sum(v for _, v in C)
        s = g.ref_musd.iloc[0]
        s = s if pd.notna(s) else sale.get((gv, int(fy)), np.nan)
        if len(X) == len(C) and all(near(a[1], b[1]) and (a[0] is None or b[0] is None or a[0] == b[0])
                                    for a, b in zip(sorted(X, key=lambda r: r[1]), sorted(C, key=lambda r: r[1]))):
            cat = "exact"
        elif near(tx, tc) and (_partition(C, X) if len(C) <= len(X) else _partition(X, C)):
            cat = "equivalent"
        elif near(tx, tc) and (_partition(C, X, False) if len(C) <= len(X) else _partition(X, C, False)):
            cat = "relabelled"
        elif near(sum(v for _, v in C), s if pd.notna(s) else tc) and _covers(C, ALLX.get((gv, int(fy)), [])):
            cat = "other_cut"
        elif near(tx, tc) and _countries_agree(C, ALLX.get((gv, int(fy)), [])):
            cat = "countries_agree"
        elif pd.notna(s) and tx < s * (1 - TOL):
            ok = all(_subset(v, [(j, cv) for j, (cl, cv) in enumerate(C) if lab is None or cl == lab], TOL) for lab, v in X)
            cat = "partial" if ok else "partial_unchecked"
        elif near(tx, tc):
            cat = "total_only"
        else:
            cat = "differ"
        cx = {i for i, _ in X if i and i != "USA"}
        cc = {a for a, _ in C if a and a != "USA"}
        rows.append((gv, int(fy), cat, tx, tc, s, len(X), len(C), len(cx), len(cc), len(cx & cc)))
    return pd.DataFrame(rows, columns=["gvkey", "fyear", "category", "total_xbrl", "total_cs", "sale", "rows_xbrl", "rows_cs",
                                       "countries_xbrl", "countries_cs", "countries_both"])


def public() -> int:
    """Write the table with no Compustat input, for release: no GVKEY, and no Compustat sales as the reference
    total of a filing without a revenue fact of its own (those filings keep a missing reference and skip the
    sales-gap step of `nesting`). The rules are the frozen ones; nothing else changes."""
    xb = xbrl(pd.Series(dtype=float)).drop(columns="gvkey")
    out = I / "fsn_geo_revenue_public.parquet"
    xb.to_parquet(out, index=False)
    print("\n".join(LINES))
    print(f"- rows out: {len(xb):,} firm-year-member rows, {xb.adsh.nunique():,} filings, written to {out.name}")
    return 0


def main() -> int:
    if "--public" in sys.argv:
        return public()
    say("# XBRL revenue by geography against Compustat geographic segments")
    say()
    sale = funda_sale()
    xb = xbrl(sale)
    xb.to_parquet(I / "fsn_geo_revenue.parquet", index=False)
    say(f"- rows out: {len(xb):,} firm-year-member rows, {xb.groupby(['cik', 'fyear']).ngroups:,} filer-years")
    say("- member kinds: " + ", ".join(f"{k} {v:,}" for k, v in xb.kind.value_counts().items()))
    fy = xb.groupby(["cik", "fyear"]).agg(country=("kind", lambda k: (k == "country").any()))
    say(f"- filer-years naming at least one country: {fy.country.mean():.1%}")
    cs = compustat()
    say(f"- Compustat first-reported geographic segments: {cs.groupby(['gvkey', 'fyear']).ngroups:,} firm-years, fiscal {cs.fyear.min()}-{cs.fyear.max()}")
    res = compare(xb, cs, sale)
    res.to_parquet(I / "fsn_geo_validation.parquet", index=False)
    say()
    cats = ["exact", "equivalent", "other_cut", "countries_agree", "relabelled", "partial", "partial_unchecked", "total_only", "differ"]
    agree = ["exact", "equivalent", "other_cut", "countries_agree"]
    say("| fiscal year | firm-years in both | " + " | ".join(cats) + " | agree | agree or partial |")
    say("|:--|--:|" + "--:|" * (len(cats) + 2))
    split = [(f"tune {TUNE[0]}-{TUNE[1]}", res[res.fyear.between(*TUNE)]), (f"test {TUNE[1] + 1} on", res[res.fyear > TUNE[1]])]
    shown = res if SHOW_TEST else res[res.fyear <= TUNE[1]]
    if not SHOW_TEST:
        split = split[:1]
        say(f"(held-out fiscal years after {TUNE[1]} are computed and stored but not shown; pass --show-test once the rules are frozen)")
    for y, g in [*shown.groupby("fyear"), *[(k, g) for k, g in split if len(g)]]:
        sh = g.category.value_counts(normalize=True)
        say(f"| {y} | {len(g):,} | " + " | ".join(f"{sh.get(c, 0):.1%}" for c in cats)
            + f" | {sum(sh.get(c, 0) for c in agree):.1%} | {sum(sh.get(c, 0) for c in agree + ['partial']):.1%} |")
    say()
    say("- nested members set aside: " + ", ".join(f"{k} {v:,}" for k, v in xb.nested.value_counts().items() if k))
    named = res[(res.countries_cs > 0) | (res.countries_xbrl > 0)]
    if len(named):
        say()
        say(f"- firm-years where either side names a foreign country: {len(named):,}; countries named by both: "
            f"{named.countries_both.sum():,} of {named.countries_cs.sum():,} Compustat and {named.countries_xbrl.sum():,} XBRL")
    say()
    say("Coverage of XBRL filer-years with revenue by geography: " + ", ".join(f"{y} {n:,}" for y, n in xb.groupby("fyear").cik.nunique().items() if y >= 2008))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
