"""The common posting record every source adapter returns."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that only track where a click came from; they never identify a job.
_TRACKING_PARAMS = {"ref", "source", "src", "gh_src", "lever-source", "lever-origin", "utm"}


def canonical_url(url: str) -> str:
    """Normalise a posting URL so the same job always hashes to the same id."""
    parts = urlsplit(url.strip())
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.lower() or "https", parts.netloc.lower(), path, urlencode(query), "")
    )


def posting_id(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()


def parse_time(value: Any) -> datetime | None:
    """Accept ISO strings, epoch seconds or epoch milliseconds. Always returns UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Posting:
    source: str  # greenhouse | lever | ashby | usajobs | adzuna
    company: str
    title: str
    url: str
    text: str = ""  # cleaned description, sent to the classifier
    locations: list[str] = field(default_factory=list)
    workplace: str | None = None  # remote | hybrid | onsite | None (unknown)
    posted_at: datetime | None = None
    salary_min: float | None = None  # annual, in salary_currency
    salary_max: float | None = None
    salary_currency: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = posting_id(self.url)

    @property
    def location(self) -> str:
        return "; ".join(self.locations)

    @property
    def remote(self) -> bool:
        return self.workplace == "remote"

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        return row
