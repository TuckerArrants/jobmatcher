import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jobmatcher.sources.http import NotFound

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 7, tzinfo=timezone.utc)


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


class FakeHttp:
    """Serves fixture JSON by URL prefix; anything else is a 404. Records every call."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append(url)
        for prefix, data in self.routes.items():
            if url.startswith(prefix):
                return data
        raise NotFound(url)


@pytest.fixture
def fake_http():
    return FakeHttp(
        {
            "https://boards-api.greenhouse.io/v1/boards/examplebio/": fixture("greenhouse.json"),
            "https://api.lever.co/v0/postings/examplefund": fixture("lever.json"),
            "https://api.ashbyhq.com/posting-api/job-board/examplechem": fixture("ashby.json"),
        }
    )


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never touch the network."""
    import requests

    def refuse(*a, **k):
        raise RuntimeError("network access in tests")

    monkeypatch.setattr(requests.Session, "request", refuse)
