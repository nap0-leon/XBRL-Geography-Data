"""The countries a geographic name holds: XBRL region members (`AsiaPacific`, `EMEA`) and Compustat region codes.

Membership comes from the Atlas country groups (`raw/atlas_product_space/location_group.csv`: continents,
subregions, World Bank regions). A name is read by the first pattern in `PATTERNS` that matches its words; each
pattern names the groups whose union it holds, minus the groups after a `-`. A name no pattern matches holds
`None`: unknown, never empty.

    holds("Asia Pacific")            # set of ISO3 codes in Asia and Oceania
    holds("N_AMER")                  # Compustat code, read the same way
    contains("Europe Middle East And Africa", "DEU")   # True
"""

from __future__ import annotations

import re
from functools import lru_cache

import pandas as pd

from core import config

ALL = "world:world"
# (pattern on the lower-case words, groups to add, groups to take away). Order matters: the first match wins.
PATTERNS: list[tuple[str, list[str], list[str]]] = [
    (r"\b(non ?u ?s|international|foreign|outside|rest of (the )?world|r_world|all other|other countries|other foreign|other international)\b",
     [ALL], ["iso:USA"]),
    (r"\b(emea|europe,? middle east (and|&) africa|europe middle east africa|europe and africa)\b",
     ["continent:Europe", "continent:Africa", "wdi_region:Middle East & North Africa", "subregion:Western Asia"], []),
    (r"\b(middle east|mid_east|mena)\b", ["wdi_region:Middle East & North Africa", "subregion:Western Asia"], []),
    (r"\b(asia ?pacific|apac|pac_rim|pacific rim|asia and pacific|asia pacific|austr_as|pac_bas|far east|pacific)\b",
     ["continent:Asia", "continent:Oceania"], []),
    (r"\b(latin america|l_amer|south and central america|central and south america)\b", ["wdi_region:Latin America & Caribbean"], ["iso:USA", "iso:CAN"]),
    (r"\b(south america|s_amer)\b", ["continent:South America"], []),
    (r"\b(central america|c_amer)\b", ["subregion:Central America"], []),
    (r"\b(caribbean|carib)\b", ["subregion:Caribbean"], []),
    (r"\b(north america|n_amer|northern america)\b", ["continent:North America", "subregion:Northern America"], []),
    (r"\b(americas|western hemisphere|w_hem)\b", ["region:Americas"], []),
    (r"\b(scandinavia|nordic|nordics|scand)\b", ["subregion:Northern Europe"], []),
    (r"\b(europe|eur_un|w_eur|e_eur|no_europ|so_europ|benelux)\b", ["continent:Europe"], []),
    (r"\b(africa|so_afric|no_afric|w_africa|e_africa|c_africa)\b", ["continent:Africa"], []),
    (r"\b(oceania|australasia|australia and new zealand)\b", ["continent:Oceania"], []),
    (r"\b(asia|se_asia|e_asia|so_asia|c_asia|eurasia)\b", ["continent:Asia"], []),
]


@lru_cache(maxsize=1)
def _groups() -> dict[str, frozenset[str]]:
    g = pd.read_csv(config.RAW / "atlas_product_space" / "location_group.csv", dtype=str, keep_default_na=False)
    out = {f"{t}:{n}": frozenset(s.country_iso3_code) for (t, n), s in g.groupby(["group_type", "group_name"])}
    out.update({f"iso:{c}": frozenset([c]) for c in g.country_iso3_code.unique()})
    return out


def _words(name: str) -> str:
    w = re.sub(r"Member$", "", str(name))
    w = re.sub(r"(?<=[a-z])(and|of|the)(?=[A-Z])", r" \1 ", w)   # "UnitedKingdomandIreland"
    w = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", w)
    return re.sub(r"\s+", " ", w).strip().lower()


@lru_cache(maxsize=1)
def _country_names() -> dict[str, str]:
    """Country names without spaces or punctuation, lower case, to ISO3 (Atlas names, short and long)."""
    c = pd.read_csv(config.RAW / "atlas_product_space" / "location_country.csv", dtype=str, keep_default_na=False)
    out = {}
    for col in ("country_name", "country_name_short"):
        out.update({re.sub(r"[^a-z]", "", n.lower()): i for n, i in zip(c[col], c.country_iso3_code)})
    out.update({"us": "USA", "usa": "USA", "unitedstates": "USA", "uk": "GBR", "unitedkingdom": "GBR", "china": "CHN",
                "prc": "CHN", "korea": "KOR", "southkorea": "KOR", "taiwan": "TWN", "russia": "RUS", "hongkong": "HKG"})
    return out


def _piece(w: str) -> frozenset[str] | None:
    """The countries one piece holds: the union of the region patterns it matches, else a country name."""
    gr = _groups()
    hits = [(add, sub) for pat, add, sub in PATTERNS[1:] if re.search(pat, w)]
    if hits:
        s = frozenset()
        for add, sub in hits:
            s |= frozenset().union(*(gr.get(a, frozenset()) for a in add)) - frozenset().union(*(gr.get(b, frozenset()) for b in sub))
        return s
    key = re.sub(r"[^a-z]", "", w)
    names = _country_names()
    return frozenset([names[key]]) if key in names else None


def _part(w: str) -> frozenset[str] | None:
    """The countries one clause holds. "Non US", "international" and the like hold the rest of the world whatever
    else they say. Otherwise the clause is split on "and", "&" and commas ("North and South America", "South America
    and Canada", "United Kingdom and Ireland") and holds the union of its pieces; one piece nothing reads makes the
    clause unknown."""
    gr = _groups()
    if re.search(PATTERNS[0][0], w):
        return frozenset(gr[ALL]) - gr["iso:USA"]
    whole = _piece(w)
    pieces = [p.strip() for p in re.split(r"\band\b|&|,", w) if p.strip()]
    if len(pieces) <= 1:
        return whole
    # "north and south america": a piece may borrow the last word of the next ("north" + "america")
    tail = pieces[-1].split()[-1]
    parts = [_piece(p) or _piece(f"{p} {tail}") for p in pieces]
    if all(x is not None for x in parts):
        return frozenset().union(*parts)
    return whole


@lru_cache(maxsize=4096)
def holds(name: str) -> frozenset[str] | None:
    """ISO3 codes the name holds, or None when nothing reads it. "Asia Other Than China" is Asia less China."""
    w = _words(name)
    m = re.split(r"\b(?:excluding|except|other than|outside of|ex)\b", w, maxsplit=1)
    s = _part(m[0])
    if s is not None and len(m) == 2:
        out = _part(m[1].strip())
        if out:
            s = s - out
    return s


def contains(name: str, iso3: str) -> bool:
    s = holds(name)
    return bool(s) and iso3 in s
