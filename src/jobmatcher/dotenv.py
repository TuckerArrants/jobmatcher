"""Load KEY=VALUE lines from a local .env file into the environment.

For running on your own machine: put your keys in `.env` at the repository root. That file is
always git-ignored and the privacy check blocks it if it is ever staged. Variables already set
in the environment win over the file. Values are never printed.
"""

from __future__ import annotations

import os
from pathlib import Path


def load(path: Path | str = ".env") -> list[str]:
    """Returns the names (never the values) of variables that were set from the file."""
    path = Path(path)
    if not path.is_file():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
