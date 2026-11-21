"""Geography shared by the county and district tables: state codes, the Congress of a year, the district map of a Congress.

A congressional district is written `ST-NN` (`TX-07`); a state with one seat is `ST-00`. A county is its five-digit FIPS code.
"""

from __future__ import annotations

STATE_FIPS = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID",
    "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO",
    "30": "MT", "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK", "41": "OR", "42": "PA",
    "44": "RI", "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY",
}
FIPS_OF = {v: k for k, v in STATE_FIPS.items()}

# The file each Congress's districts are read from. 110 on: the Census API's county parts (`raw/census_cd/cd<map>`).
# 106, 108 and 109: the Geocorr 2000 file placed by hand (`raw/census_cd_manual`), on 2000 census population. A Congress
# between two files takes the file in force. The 103rd to 107th all read the 106th map (the 1998 election): the
# 1990s districts were redrawn by court order in several states, listed in APPROX.
MAP_OF = {103: 106, 104: 106, 105: 106, 106: 106, 107: 106, 108: 108, 109: 109, 110: 110, 111: 111, 112: 111, 113: 113, 114: 113, 115: 115, 116: 116, 117: 116, 118: 118, 119: 119}
# States whose districts differ between the Congress and the file it borrows: weights there are approximate.
#   1990s: Louisiana redrew for 1994 and 1996, Georgia, Florida and Texas for 1996, North Carolina, Virginia and New York for 1998,
#   North Carolina again for 2000. 2020s: North Carolina redrew for 2020.
APPROX = {103: ("LA", "GA", "FL", "TX", "NC", "VA", "NY"), 104: ("LA", "GA", "FL", "TX", "NC", "VA", "NY"), 105: ("NC", "VA", "NY"), 107: ("NC",), 117: ("NC",)}
GEOCORR_MAPS = (106, 108, 109)
# Seats in the House by the apportionment a map belongs to, for the unit tests.
HOUSE_SEATS = 435


def congress_of(year: int) -> int:
    """The Congress sitting in a calendar year: the 116th sat in 2019 and 2020."""
    return (int(year) - 1789) // 2 + 1


def years_of(congress: int) -> tuple[int, int]:
    y = 1789 + 2 * (int(congress) - 1)
    return y, y + 1


def district(state: str, number: object) -> str:
    """`ST-NN`; a single-seat state, however its source codes it (0, 1 or 98), is `ST-00` only when the caller passes 0."""
    return f"{state}-{int(number):02d}"

# County codes the employment files use that a district map does not hold, and the county of the map that stands in.
# Connecticut's nine planning regions replaced its eight counties in federal statistics from 2022; the maps on disk are on
# the 2020 census counties. Each region takes the county that holds most of its residents: an approximation, flagged by
# the caller. Broomfield, Colorado was formed in 2001 from four counties, most of it from Boulder.
COUNTY_STAND_IN = {
    "09110": "09003", "09120": "09001", "09130": "09007", "09140": "09009", "09150": "09015", "09160": "09005", "09170": "09009", "09180": "09011", "09190": "09001",
    "08014": "08013",
}
