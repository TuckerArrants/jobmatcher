"""A small, polite HTTP client shared by the source adapters."""

from __future__ import annotations

import time
from typing import Any

import requests

from jobmatcher import __version__

USER_AGENT = f"jobmatcher/{__version__} (open-source job matcher; one request per board per run)"
RETRY_STATUS = {429, 500, 502, 503, 504, 529}


class NotFound(Exception):
    """The board or endpoint does not exist (HTTP 404)."""


class SourceError(Exception):
    """A request failed after retries."""


class Http:
    def __init__(
        self,
        timeout: float = 20.0,
        retries: int = 3,
        backoff: float = 2.0,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self._sleep = sleep

    def get_json(
        self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> Any:
        last: str = ""
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last = type(exc).__name__
            else:
                if resp.status_code == 404:
                    raise NotFound(url)
                if resp.status_code < 400:
                    return resp.json()
                if resp.status_code not in RETRY_STATUS:
                    raise SourceError(f"HTTP {resp.status_code}")
                last = f"HTTP {resp.status_code}"
                retry_after = resp.headers.get("Retry-After", "")
                if attempt < self.retries and retry_after.isdigit():
                    self._sleep(min(float(retry_after), 60.0))
                    continue
            if attempt < self.retries:
                self._sleep(self.backoff * (2**attempt))
        raise SourceError(f"giving up after {self.retries + 1} attempts ({last})")
