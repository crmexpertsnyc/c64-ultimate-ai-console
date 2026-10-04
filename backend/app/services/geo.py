"""Places for events: one spelling per country, US states / Canadian provinces picked out of "City, ST" strings,
and whether an event is Commodore-specific or retro in general."""

from __future__ import annotations

import re

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}
CA_PROVINCES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia", "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec", "SK": "Saskatchewan",
}
_REGIONS = {"United States": US_STATES, "Canada": CA_PROVINCES}
_BY_NAME = {name.lower(): (code, country) for country, table in _REGIONS.items() for code, name in table.items()}

_COUNTRY_ALIASES = {
    "us": "United States", "usa": "United States", "u.s": "United States",
    "u.s.a": "United States", "u.s.": "United States", "u.s.a.": "United States",
    "united states of america": "United States", "america": "United States", "the united states": "United States",
    "uk": "United Kingdom", "u.k.": "United Kingdom", "great britain": "United Kingdom", "england": "United Kingdom",
    "scotland": "United Kingdom", "wales": "United Kingdom", "northern ireland": "United Kingdom", "britain": "United Kingdom",
    "deutschland": "Germany", "the netherlands": "Netherlands", "holland": "Netherlands", "czechia": "Czech Republic",
    "online": "Online", "internet": "Online", "worldwide": "Online", "virtual": "Online",
}
FLAGS = {"United States": "🇺🇸", "Canada": "🇨🇦", "United Kingdom": "🇬🇧", "Germany": "🇩🇪", "Netherlands": "🇳🇱",
         "Sweden": "🇸🇪", "Norway": "🇳🇴", "Denmark": "🇩🇰", "Finland": "🇫🇮", "Poland": "🇵🇱", "Italy": "🇮🇹",
         "France": "🇫🇷", "Spain": "🇪🇸", "Hungary": "🇭🇺", "Czech Republic": "🇨🇿", "Slovakia": "🇸🇰", "Austria": "🇦🇹",
         "Switzerland": "🇨🇭", "Belgium": "🇧🇪", "Australia": "🇦🇺", "Argentina": "🇦🇷", "Brazil": "🇧🇷", "Japan": "🇯🇵",
         "Online": "🌐"}

COMMODORE = re.compile(r"\b(c64|c-64|commodore|cbm|amiga|vic-?20|c128|plus/?4|pet|sid|demo ?party|demoparty|"
                       r"scene|cracktro|commvex|world of commodore|pacommex|csdb)\b", re.I)


def normalize_country(country: str | None) -> str | None:
    if not country:
        return None
    c = re.sub(r"\s+", " ", country).strip(" .,")
    if not c:
        return None
    key = c.lower()
    if key in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[key]
    if key.upper() in US_STATES and len(key) == 2:             # "NV" given as the country
        return "United States"
    if key in _BY_NAME:
        return _BY_NAME[key][1]
    return c if c[0].isupper() else c.title()


def place(city: str | None, country: str | None) -> tuple[str | None, str | None, str | None]:
    """→ (city, region code, country). "Las Vegas, NV" / "Portland, Oregon, USA" / "Toronto, ON" all work."""
    country = normalize_country(country)
    region = None
    city = (city or "").strip() or None
    if city:
        parts = [p.strip() for p in city.split(",") if p.strip()]
        while len(parts) > 1 and normalize_country(parts[-1]) in ("United States", "Canada", "United Kingdom") \
                and parts[-1].upper() not in US_STATES and parts[-1].lower() not in _BY_NAME:
            country = country or normalize_country(parts[-1])
            parts.pop()
        if len(parts) > 1:
            last = parts[-1]
            tail = re.sub(r"\s+\d{5}(?:-\d{4})?$", "", last).strip()          # "NV 89109" → "NV"
            code = tail.upper() if tail.upper() in US_STATES or tail.upper() in CA_PROVINCES else None
            if code and country in (None, "United States", "Canada"):
                ctry = "United States" if code in US_STATES and country != "Canada" else "Canada"
                if code in (US_STATES if ctry == "United States" else CA_PROVINCES):
                    region, country = code, ctry
                    parts.pop()
            elif tail.lower() in _BY_NAME:
                code, ctry = _BY_NAME[tail.lower()]
                if country in (None, ctry):
                    region, country = code, ctry
                    parts.pop()
        city = ", ".join(parts) or None
    return city, region, country


def scope_of(*texts: str | None) -> str:
    """'commodore' for C64 / Commodore / Amiga / demoscene events, else 'retro'."""
    return "commodore" if COMMODORE.search(" ".join(t for t in texts if t)) else "retro"


def region_name(country: str | None, code: str | None) -> str | None:
    return _REGIONS.get(country or "", {}).get(code or "")
