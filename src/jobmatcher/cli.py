"""Command line: ``jobmatch <command>``. Run ``jobmatch -h`` for the list."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from jobmatcher import __version__, out
from jobmatcher.config import ConfigError, Paths


def _read_names(args: argparse.Namespace) -> list[str]:
    names = list(args.company or [])
    if args.file:
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                names.append(line)
    return names


def _cmd_find_board(args: argparse.Namespace) -> int:
    from jobmatcher.findboard import add_to_companies, best, find_board
    from jobmatcher.sources.http import Http

    names = _read_names(args)
    if not names:
        out.error("give company names, or --file with one name per line")
        return 2
    http = Http(retries=1)
    found, missing = [], []
    for name in names:
        hits = find_board(name, http, tokens=args.token or None)
        top = best(hits)
        if top is None:
            missing.append(name)
            out.detail(f"# {name}: no public Greenhouse, Lever or Ashby board found")
            continue
        found.append({"name": name, "ats": top.ats, "token": top.token})
        out.detail(f"- name: {name}\n  ats: {top.ats}\n  token: {top.token}    # {top.jobs} open jobs")
    out.info(f"boards found: {len(found)} of {len(names)}")
    if args.write:
        paths = Paths.from_env(args.user_dir)
        added = add_to_companies(paths.companies, found)
        out.info(f"added {added} to companies.yaml")
        if missing:
            note = paths.user / "reports" / "boards-not-found.txt"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text("\n".join(missing) + "\n", encoding="utf-8")
            out.info(f"{len(missing)} not found; names written to reports/boards-not-found.txt")
    return 0 if found else 1


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


def _cmd_run(args: argparse.Namespace) -> int:
    """The scheduled pipeline. Grows as milestones land: ingest -> classify -> decide -> report."""
    return _cmd_ingest(args)


def _cmd_privacy(args: argparse.Namespace) -> int:
    from jobmatcher import privacy

    return privacy.main([str(args.repo)])


def _cmd_make_private(args: argparse.Namespace) -> int:
    from jobmatcher import privatecopy

    changed = privatecopy.convert(args.repo)
    out.info("converted to a private copy" if changed else "already a private copy")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobmatch", description=__doc__)
    p.add_argument("--version", action="version", version=f"jobmatcher {__version__}")
    p.add_argument("--user-dir", help="folder with your profile and data (default: ./user)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("find-board", help="find companies' public job boards and tokens")
    s.add_argument("company", nargs="*")
    s.add_argument("--file", help="text file with one company name per line")
    s.add_argument("--token", action="append", help="token to try (repeatable)")
    s.add_argument("--write", action="store_true", help="append what is found to user/companies.yaml")
    s.set_defaults(func=_cmd_find_board)

    s = sub.add_parser("ingest", help="fetch boards, store new postings, apply the prefilter")
    s.set_defaults(func=_cmd_ingest)

    s = sub.add_parser("run", help="the full scheduled pipeline")
    s.set_defaults(func=_cmd_run)

    s = sub.add_parser("privacy-check", help="fail if personal data, databases or keys are tracked")
    s.add_argument("repo", nargs="?", default=Path("."), type=Path)
    s.set_defaults(func=_cmd_privacy)

    s = sub.add_parser("make-private", help="turn this clone into a private copy (run only in a private repo)")
    s.add_argument("repo", nargs="?", default=Path("."), type=Path)
    s.set_defaults(func=_cmd_make_private)
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
