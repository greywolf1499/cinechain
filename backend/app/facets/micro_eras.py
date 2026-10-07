"""Inclusive, overlapping release-era definitions (version 1)."""

MICRO_ERAS = {
    "silent": (None, 1928, None),
    "pre_code": (1929, 1934, "US"),
    "golden_age": (1930, 1959, None),
    "french_new_wave": (1958, 1968, "FR"),
    "new_hollywood": (1967, 1980, "US"),
    "hk_new_wave": (1979, 1990, "HK"),
    "blockbuster": (1975, 1999, "US"),
    "dogme": (1995, 2005, "DK"),
    "streaming": (2013, None, None),
}


def micro_eras(year: int | None, countries: list[str] | None) -> list[str] | None:
    if year is None or countries is None:
        return None
    return [
        name
        for name, (low, high, country) in MICRO_ERAS.items()
        if (low is None or year >= low)
        and (high is None or year <= high)
        and (country is None or country in countries)
    ]
