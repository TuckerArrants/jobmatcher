"""Console output with a privacy rule built in.

Workflow logs can be read by anyone who can see the repository. So on CI, only counts and costs
are printed; anything that could identify the user's choices (company names, titles, labels,
scores) goes through ``detail`` and is shown only in a local terminal.
"""

from __future__ import annotations

import os
import sys


def on_ci() -> bool:
    return bool(os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"))


def info(msg: str) -> None:
    """Counts, costs, status. Safe to print anywhere."""
    print(msg, flush=True)


def detail(msg: str) -> None:
    """Posting-level or personal detail. Suppressed on CI."""
    if not on_ci():
        print(msg, flush=True)


def error(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr, flush=True)
