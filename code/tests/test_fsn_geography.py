"""The geographic reading of XBRL revenue: region names, nested members, and the comparison with Compustat.

Each case is a fiscal-2020 filing whose 10-K was read during tuning (build/03zc_adjudicate_fsn_geography.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from core import config, regions

_P = Path(__file__).resolve().parent.parent / "build" / "03zb_validate_fsn_geography.py"
_s = importlib.util.spec_from_file_location("fsn_geo", _P)
V = importlib.util.module_from_spec(_s)
_s.loader.exec_module(V)

needs_atlas = pytest.mark.skipif(not (config.RAW / "atlas_product_space" / "location_group.csv").exists(),
                                 reason="Atlas country groups not on disk")


def table(rows: list[tuple[str, str, str | None, float]]) -> pd.DataFrame:
    """Members as the build sees them: member, kind, ISO3, revenue in $m."""
    d = pd.DataFrame(rows, columns=["member", "kind", "iso3", "revenue_musd"])
    return d.assign(member_words=d.member.map(V.words) if hasattr(V, "words") else d.member)


def kept(g: pd.DataFrame) -> set[str]:
    return set(g.loc[g.nested == "", "member"])


@needs_atlas
def test_region_names():
    assert regions.holds("UnitedKingdomandIreland") == frozenset({"GBR", "IRL"})
    assert {"USA", "CAN"} <= regions.holds("NorthandSouthAmerica")
    assert "CAN" in regions.holds("SouthAmericaAndCanada")
    assert "USA" not in regions.holds("AmericasExcludingUnitedStates")
    assert "USA" not in regions.holds("NonUs") and "CHN" in regions.holds("NonUs")
    assert "DEU" in regions.holds("EMEA") and "CHN" not in regions.holds("EMEA")
    assert regions.holds("ExportsFromUSToOtherRegions") is None


@needs_atlas
def test_subtotal_by_exact_sum_needs_no_sales():
    # Bank of America 2020: Total Non-U.S. next to its three regions; Compustat sales are not the table's total
    g = V.nesting(table([("US", "domestic", "USA", 75576), ("Asia", "region", None, 4232), ("EMEA", "region", None, 4491),
                         ("LatinAmerica", "region", None, 1229), ("NonUs", "foreign_total", None, 9952)]), float("nan"))
    assert kept(g) == {"US", "Asia", "EMEA", "LatinAmerica"}
    assert g.set_index("member").at["NonUs", "nested"] == "subtotal"


@needs_atlas
def test_no_set_aside_when_the_filing_total_is_met():
    # Valero 2020: the table sums to the filing's revenue (with excise taxes); nothing double counts
    g = V.nesting(table([("US", "domestic", "USA", 45174), ("CA", "country", "CAN", 4294),
                         ("UnitedKingdomandIreland", "region", None, 9268), ("OtherCountries", "region", None, 6176)]), 64912)
    assert (g.nested == "").all()


@needs_atlas
def test_children_listed_next_to_parents():
    # Chart 2020: US inside NorthAmerica and China inside AsiaPacific, parts that do not sum to the parents
    g = V.nesting(table([("AsiaPacific", "region", None, 173.8), ("CN", "country", "CHN", 100.7),
                         ("EuropeMiddleEastAfricaAndIndia", "region", None, 361.0), ("NonUs", "foreign_total", None, 9.2),
                         ("NorthAmerica", "region", None, 633.1), ("US", "domestic", "USA", 576.8)]), 1177.1)
    assert kept(g) == {"AsiaPacific", "EuropeMiddleEastAfricaAndIndia", "NonUs", "NorthAmerica"}


@needs_atlas
def test_us_breakdown_and_duplicates():
    # Host Hotels 2020: US markets that sum to the US; NonUs next to Canada and Brazil
    cities = [(c, "region", None, v) for c, v in (("Atlanta", 300.0), ("Boston", 500.0), ("Phoenix", 800.0))]
    g = V.nesting(table([("US", "domestic", "USA", 1600.0), ("CA", "country", "CAN", 13.0), ("BR", "country", "BRA", 7.0),
                         ("NonUs", "foreign_total", None, 20.0), *cities]), 1620.0)
    assert kept(g) == {"US", "CA", "BR"}
    # one total under two names
    d = V.nesting(table([("US", "domestic", "USA", 1777.4), ("International", "foreign_total", None, 1420.7),
                         ("NonUs", "foreign_total", None, 1420.7)]), 3198.1)
    assert len(kept(d)) == 2 and (d.nested == "duplicate").sum() == 1


@needs_atlas
def test_compare_categories():
    xb = table([("US", "domestic", "USA", 1816.7), ("NorthAmerica", "region", None, 3625.2), ("Europe", "region", None, 550.5)])
    # gvkey 117861, 2020: NorthAmerica holds the US and Mexico; Compustat names both countries
    xb = xb.assign(gvkey="000001", fyear=2020, nested="", ref_musd=4175.7, state_table=False)
    xb.loc[xb.member == "US", "nested"] = "part_of_listed_parent"
    cs = pd.DataFrame({"gvkey": "000001", "fyear": 2020, "sid": [1, 2, 3], "gareat": ["ISO", "ISO", "REG"],
                       "gareag": ["USA", "MEX", "EUROPE"], "sales": [1816.7, 1808.5, 550.5]})
    r = V.compare(xb, cs, pd.Series(dtype=float))
    assert r.category.iloc[0] == "equivalent"       # NorthAmerica = USA + Mexico
