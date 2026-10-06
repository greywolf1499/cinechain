"""Canonical country parsing, including legacy delimiters and historical codes."""

import json
import re

LEGACY_COUNTRY_CODES = {
    "SU": "RU",
    "YU": "RS",
    "CS": "RS",
    "XC": "CZ",
    "DD": "DE",
    "AN": "CW",
}
_CODE_RE = re.compile(r"^[A-Z]{2}$")


def parse_country_codes(raw: str | None) -> list[tuple[str, str | None]]:
    """Unique normalized codes with provenance for Passport's historical-code map."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        values = parsed if isinstance(parsed, list) else [parsed]
    except (TypeError, ValueError):
        values = re.split(r"[,;|/\s]+", raw)
    seen: dict[str, str | None] = {}
    for value in values:
        code = str(value).strip().upper()
        legacy = None
        if code in LEGACY_COUNTRY_CODES:
            legacy, code = code, LEGACY_COUNTRY_CODES[code]
        if _CODE_RE.match(code) and code not in seen:
            seen[code] = legacy
    return list(seen.items())


def parse_countries(raw: str | None) -> list[str]:
    return [code for code, _legacy in parse_country_codes(raw)]
