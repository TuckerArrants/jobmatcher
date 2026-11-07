"""Privacy checks on what git tracks.

Standard library only, so the pre-commit hook can run it without installing the package:

    python3 src/jobmatcher/privacy.py [repo_root]

Two modes:

* **Upstream** (the public repository): nothing personal may be tracked. No ``user/`` folder,
  no databases, no label files outside the synthetic examples, no ``.env`` files, no keys. If a
  local ``user/`` folder exists, its distinctive values (home zip, long profile lines) must not
  appear in any tracked file.
* **Private copy** (marked by the file ``.jobmatcher-private`` at the root, which
  ``jobmatch init`` creates): ``user/`` may be tracked, but databases, ``.env`` files and keys
  still may not.

The marker is itself forbidden in a repository marked as a GitHub template (checked in CI), so
it cannot silently switch the public upstream into the relaxed mode.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PRIVATE_MARKER = ".jobmatcher-private"

# Paths that may contain synthetic, clearly fictional data.
_SYNTHETIC_PREFIXES = ("user.example/", "tests/")

_KEY_PATTERN = re.compile(
    r"\b(TYPESAFE_API_KEY|USAJOBS_API_KEY|ADZUNA_APP_KEY|ADZUNA_APP_ID)\s*[:=]\s*['\"]?"
    r"(?!\$|\{|<|your|xxx|\.\.\.)[A-Za-z0-9_\-]{12,}",
    re.IGNORECASE,
)
_ZIP_LINE = re.compile(r"^\s*home_zip\s*:\s*['\"]?(\d{5})", re.MULTILINE)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def tracked_files(repo: Path) -> list[str]:
    """Every path in the git index: committed files plus anything staged."""
    out = _git(repo, "ls-files", "-z", "--cached")
    return [p for p in out.split("\0") if p]


def is_private_copy(repo: Path) -> bool:
    return (repo / PRIVATE_MARKER).exists()


def _personal_needles(repo: Path) -> list[str]:
    """Distinctive values from a local user/ folder that must never reach upstream files."""
    user = repo / "user"
    needles: list[str] = []
    filters = user / "filters.yaml"
    if filters.exists():
        needles += _ZIP_LINE.findall(filters.read_text(encoding="utf-8", errors="ignore"))
    profile = user / "profile.md"
    if profile.exists():
        for line in profile.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip(" -*#>\t")
            if len(line) >= 40:  # long, specific lines only; short ones collide with templates
                needles.append(line)
    return needles


def check(repo: Path | str = ".") -> list[str]:
    """Return a list of problems; empty means clean."""
    repo = Path(repo).resolve()
    files = tracked_files(repo)
    private = is_private_copy(repo)
    problems: list[str] = []

    for path in files:
        low = path.lower()
        name = low.rsplit("/", 1)[-1]
        synthetic = low.startswith(_SYNTHETIC_PREFIXES)
        if low.endswith((".sqlite", ".sqlite-journal", ".db")):
            problems.append(f"database tracked: {path}")
        if name == ".env" or name.startswith(".env."):
            problems.append(f"env file tracked: {path}")
        if private:
            continue
        if low.startswith("user/"):
            problems.append(f"personal folder tracked: {path}")
        elif not synthetic and re.search(r"(^|/)labels[^/]*\.(csv|jsonl?)$", low):
            problems.append(f"label file tracked: {path}")
        elif not synthetic and re.search(r"(^|/)reports/\d{4}-\d{2}-\d{2}", low):
            problems.append(f"report tracked: {path}")

    needles = [] if private else _personal_needles(repo)
    for path in files:
        full = repo / path
        try:
            if full.stat().st_size > 2_000_000:
                continue
            text = full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _KEY_PATTERN.search(text):
            problems.append(f"possible API key in: {path}")
        for needle in needles:
            if needle in text:
                problems.append(f"value from your local user/ folder appears in: {path}")
                break
    return problems


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    repo = Path(argv[0]) if argv else Path(".")
    try:
        problems = check(repo)
    except subprocess.CalledProcessError:
        print("privacy check: not a git repository, nothing to check")
        return 0
    mode = "private copy" if is_private_copy(repo) else "upstream"
    if problems:
        print(f"privacy check FAILED ({mode}): {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"privacy check passed ({mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
