"""Switch a clone into "private copy" mode, so user/ can be committed there.

Only ever run this in your own private repository. It:
* removes the personal-data block from .gitignore (databases, .env and caches stay ignored);
* creates the marker file that relaxes the privacy check for user/;
* enables the pre-commit hook.

The scheduled workflow refuses to run in a public repository, so a private copy that is later
made public stops sending data anywhere.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from jobmatcher.privacy import PRIVATE_MARKER

_BLOCK = re.compile(
    r"# --- jobmatcher: personal data \(BEGIN\) ---.*?# --- jobmatcher: personal data \(END\) ---\n?",
    re.DOTALL,
)

MARKER_TEXT = """\
This repository is a PRIVATE copy of jobmatcher. Its user/ folder holds personal data.
Keep the repository private. Do not push it to a public remote or open pull requests
from it to the public project.
"""


def is_private(repo: Path) -> bool:
    return (repo / PRIVATE_MARKER).exists()


def convert(repo: Path | str) -> bool:
    """Returns True if anything changed."""
    repo = Path(repo)
    changed = False
    gitignore = repo / ".gitignore"
    text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    new = _BLOCK.sub("", text)
    if new != text:
        gitignore.write_text(new, encoding="utf-8")
        changed = True
    marker = repo / PRIVATE_MARKER
    if not marker.exists():
        marker.write_text(MARKER_TEXT, encoding="utf-8")
        changed = True
    if (repo / ".git").exists():
        subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath", ".githooks"], check=False)
    return changed
