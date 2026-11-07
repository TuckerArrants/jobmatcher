"""Command line: ``jobmatch <command>``. Run ``jobmatch -h`` for the list."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from jobmatcher import __version__, out
from jobmatcher.config import ConfigError, Paths


def _cmd_find_board(args: argparse.Namespace) -> int:
    from jobmatcher.findboard import find_board
    from jobmatcher.sources.http import Http

    hits = find_board(args.company, Http(retries=1), tokens=args.token or None)
    if not hits:
        out.info("no public Greenhouse, Lever or Ashby board found; try --token with a guess")
        return 1
    for h in hits:
        out.info(f"- name: {args.company}\n  ats: {h.ats}\n  token: {h.token}    # {h.jobs} open jobs")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    from jobmatcher.ingest import ingest
    from jobmatcher.store import Store

    paths = Paths.from_env(args.user_dir)
    store = Store(paths.db)
    try:
        result = ingest(paths, store)
    finally:
        store.close()
    c = result["counts"]
    out.info(
        f"boards: {c.get('boards_ok', 0)} ok, {c.get('boards_failed', 0)} failed | "
        f"postings: {c.get('fetched', 0)} fetched, {c.get('new', 0)} new, {c.get('kept', 0)} kept"
    )
    if result["dropped_by_rule"]:
        drops = ", ".join(f"{k} {v}" for k, v in sorted(result["dropped_by_rule"].items()))
        out.info(f"dropped by rule: {drops}")
    for line in result["failed"]:
        out.detail(f"  failed: {line}")
    return 0


def _cmd_privacy(args: argparse.Namespace) -> int:
    from jobmatcher import privacy

    return privacy.main([str(args.repo)])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobmatch", description=__doc__)
    p.add_argument("--version", action="version", version=f"jobmatcher {__version__}")
    p.add_argument("--user-dir", help="folder with your profile and data (default: ./user)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("find-board", help="find a company's public job board and token")
    s.add_argument("company")
    s.add_argument("--token", action="append", help="token to try (repeatable)")
    s.set_defaults(func=_cmd_find_board)

    s = sub.add_parser("ingest", help="fetch boards, store new postings, apply the prefilter")
    s.set_defaults(func=_cmd_ingest)

    s = sub.add_parser("privacy-check", help="fail if personal data, databases or keys are tracked")
    s.add_argument("repo", nargs="?", default=Path("."), type=Path)
    s.set_defaults(func=_cmd_privacy)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        out.error(str(exc))
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
