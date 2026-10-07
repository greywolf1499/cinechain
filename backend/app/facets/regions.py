"""UN M49 subregions, plus explicitly labelled convenience groups."""

REGIONS = {
    "northern_africa": "DZ EG LY MA SD TN EH",
    "eastern_africa": "IO BI KM DJ ER ET TF KE MG MW MU YT MZ RE RW SC SO SS UG TZ ZM ZW",
    "middle_africa": "AO CM CF TD CG CD GQ GA ST",
    "southern_africa": "BW SZ LS NA ZA",
    "western_africa": "BJ BF CV CI GM GH GN GW LR ML MR NE NG SH SN SL TG",
    "caribbean": "AI AG AW BS BB BQ VG KY CU CW DM DO GD GP HT JM MQ MS PR BL KN LC MF VC SX TT TC VI",
    "central_america": "BZ CR SV GT HN MX NI PA",
    "south_america": "AR BO BV BR CL CO EC FK GF GY PY PE GS SR UY VE",
    "northern_america": "BM CA GL PM US",
    "central_asia": "KZ KG TJ TM UZ",
    "eastern_asia": "CN HK MO KP JP MN KR TW",
    "south_eastern_asia": "BN KH ID LA MY MM PH SG TH TL VN",
    "southern_asia": "AF BD BT IN IR MV NP PK LK",
    "western_asia": "AM AZ BH CY GE IQ IL JO KW LB OM QA SA PS SY TR AE YE",
    "eastern_europe": "BY BG CZ HU PL MD RO RU SK UA",
    "northern_europe": "AX DK EE FO FI GG IS IE IM JE LV LT NO SJ SE GB",
    "southern_europe": "AL AD BA HR GI GR VA IT MT ME MK PT SM RS SI ES",
    "western_europe": "AT BE FR DE LI LU MC NL CH",
    "australia_new_zealand": "AU CX CC HM NZ NF",
    "melanesia": "FJ NC PG SB VU",
    "micronesia": "GU KI MH FM NR MP PW UM",
    "polynesia": "AS CK PF NU PN WS TK TO TV WF",
    "anglosphere": "AU CA GB IE NZ US",
}
REGIONS = {name: frozenset(codes.split()) for name, codes in REGIONS.items()}


def regions_of(countries: list[str]) -> list[str]:
    return [name for name, codes in REGIONS.items() if codes.intersection(countries)]
