"""Place US job locations on a map, offline.

Zip codes and city centroids come from the bundled ``zipcodes`` dataset (MIT). Nothing is sent
anywhere. A location string becomes a ``Place``: remote or not, a country if one can be told,
and coordinates when a US city can be found.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache

STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "DC": "district of columbia",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois",
    "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
    "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
    "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon",
    "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina", "SD": "south dakota",
    "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia",
    "WA": "washington", "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
    "PR": "puerto rico",
}
_STATE_BY_NAME = {v: k for k, v in STATES.items()}

# Common ways postings name a US metro without "City, ST".
_ALIASES = {
    "nyc": ("new york", "NY"), "new york city": ("new york", "NY"), "new york": ("new york", "NY"),
    "manhattan": ("new york", "NY"), "brooklyn": ("brooklyn", "NY"),
    "sf": ("san francisco", "CA"), "san francisco": ("san francisco", "CA"),
    "bay area": ("san francisco", "CA"), "sf bay area": ("san francisco", "CA"),
    "san francisco bay area": ("san francisco", "CA"), "silicon valley": ("san jose", "CA"),
    "washington dc": ("washington", "DC"), "washington d.c.": ("washington", "DC"),
    "dc": ("washington", "DC"), "boston": ("boston", "MA"), "seattle": ("seattle", "WA"),
    "chicago": ("chicago", "IL"), "austin": ("austin", "TX"), "los angeles": ("los angeles", "CA"),
    "la": ("los angeles", "CA"), "atlanta": ("atlanta", "GA"), "denver": ("denver", "CO"),
    "philadelphia": ("philadelphia", "PA"), "pittsburgh": ("pittsburgh", "PA"),
    "san diego": ("san diego", "CA"), "houston": ("houston", "TX"), "dallas": ("dallas", "TX"),
    "miami": ("miami", "FL"), "nashville": ("nashville", "TN"), "raleigh": ("raleigh", "NC"),
    "salt lake city": ("salt lake city", "UT"), "portland": ("portland", "OR"),
    "minneapolis": ("minneapolis", "MN"), "detroit": ("detroit", "MI"), "phoenix": ("phoenix", "AZ"),
}

_US_TOKENS = re.compile(r"\b(us|u\.s\.a?\.?|usa|united states( of america)?|america|north america|amer)\b", re.I)
_FOREIGN = re.compile(
    r"\b(canada|mexico|uk|u\.k\.|united kingdom|england|scotland|wales|ireland|germany|france|"
    r"spain|portugal|italy|netherlands|belgium|switzerland|austria|sweden|norway|denmark|finland|"
    r"poland|czech|romania|ukraine|israel|india|china|japan|korea|singapore|australia|"
    r"new zealand|brazil|argentina|colombia|chile|emea|apac|latam|europe|asia|"
    r"london|toronto|vancouver|montreal|berlin|munich|paris|amsterdam|dublin|zurich|"
    r"tel aviv|bangalore|bengaluru|hyderabad|tokyo|sydney|melbourne|sao paulo|"
    r"mexico city|warsaw|barcelona|madrid|stockholm|copenhagen)\b",
    re.I,
)
_PROVINCES = {"ON", "QC", "BC", "AB", "MB", "SK", "NS", "NB", "NL", "PE"}
_CITY_STATE = re.compile(r"([A-Za-z][A-Za-z .'\-]*?)\s*,\s*([A-Za-z][A-Za-z .]*?)\s*(?=$|,|\(|\s-\s|\s–\s)")


@dataclass(frozen=True)
class Place:
    remote: bool = False
    country: str | None = None  # "US", "foreign", or None when the string does not say
    state: str | None = None
    lat: float | None = None
    lon: float | None = None

    @property
    def located(self) -> bool:
        return self.lat is not None


@lru_cache(maxsize=1)
def _city_index() -> dict[tuple[str, str], tuple[float, float]]:
    import zipcodes

    sums: dict[tuple[str, str], list[float]] = {}
    for z in zipcodes.list_all():
        try:
            lat, lon = float(z["lat"]), float(z["long"])
        except (TypeError, ValueError):
            continue
        for city in [z["city"], *(z.get("acceptable_cities") or [])]:
            key = (city.lower(), z["state"])
            acc = sums.setdefault(key, [0.0, 0.0, 0])
            acc[0] += lat
            acc[1] += lon
            acc[2] += 1
    return {k: (v[0] / v[2], v[1] / v[2]) for k, v in sums.items()}


@lru_cache(maxsize=4096)
def zip_coords(zip_code: str) -> tuple[float, float] | None:
    import zipcodes

    try:
        found = zipcodes.matching(str(zip_code).strip()[:5])
    except (ValueError, TypeError):
        return None
    if not found:
        return None
    return float(found[0]["lat"]), float(found[0]["long"])


def city_coords(city: str, state: str) -> tuple[float, float] | None:
    city = re.sub(r"\s+", " ", city.lower().replace("st.", "saint").strip())
    idx = _city_index()
    return idx.get((city, state)) or idx.get((city.replace("saint", "st"), state))


def _state_code(token: str) -> str | None:
    t = token.strip().rstrip(".")
    if t.upper() in STATES:
        return t.upper()
    return _STATE_BY_NAME.get(t.lower())


@lru_cache(maxsize=8192)
def parse_location(text: str) -> Place:
    s = (text or "").strip()
    low = s.lower()
    remote = bool(re.search(r"\b(remote|anywhere|distributed|work from home|wfh)\b", low))
    foreign = bool(_FOREIGN.search(low))
    country = "US" if _US_TOKENS.search(s) else None

    for m in _CITY_STATE.finditer(s):
        city, region = m.group(1).strip(), m.group(2).strip()
        if region.upper() in _PROVINCES:
            foreign = True
            continue
        state = _state_code(region)
        if state:
            city = re.sub(r"^(remote|hybrid|onsite|on-site|in-office)\s*[-–:]?\s*", "", city, flags=re.I)
            coords = city_coords(city, state)
            return Place(remote, "US", state, *(coords or (None, None)))

    for alias, (city, state) in _ALIASES.items():
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", low):
            if alias in ("la", "dc", "sf") and not re.search(rf"\b{alias}\b", s, re.I):
                continue
            coords = city_coords(city, state)
            return Place(remote, "US", state, *(coords or (None, None)))

    for token in re.split(r"[,\-–()/|]", s):
        state = _state_code(token)
        if state and len(token.strip()) > 2:  # full state name, e.g. "Remote - Texas"
            return Place(remote, "US", state)

    if foreign and country != "US":
        return Place(remote, "foreign")
    return Place(remote, country)


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))
