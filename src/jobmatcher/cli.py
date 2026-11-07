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
    searches = [f"{s} {c[f'{s}_found']} found" for s in ("adzuna", "usajobs") if f"{s}_found" in c]
    searches += [f"{s} skipped (no key in .env)" for s in ("adzuna", "usajobs") if f"{s}_skipped_no_key" in c]
    if searches:
        out.info("searches: " + ", ".join(searches))
    if c.get("duplicates"):
        out.info(f"duplicates across sources dropped: {c['duplicates']}")
    if result["dropped_by_rule"]:
        drops = ", ".join(f"{k} {v}" for k, v in sorted(result["dropped_by_rule"].items()))
        out.info(f"dropped by rule: {drops}")
    for line in result["failed"]:
        out.detail(f"  failed: {line}")
    return 0


def _cmd_classify(args: argparse.Namespace) -> int:
    from jobmatcher.classify import CostLimit, classify
    from jobmatcher.classifiers.jev import JevError
    from jobmatcher.store import Store

    paths = Paths.from_env(args.user_dir)
    store = Store(paths.db)
    try:
        res = classify(paths, store, limit=args.limit, dry_run=args.dry_run, max_cost=args.max_cost)
    except CostLimit as exc:
        out.error(str(exc))
        return 3
    except JevError as exc:
        out.error(str(exc))
        return 4
    finally:
        store.close()
    if res.dry_run:
        out.info(
            f"dry run: {res.candidates} postings to classify ({res.reused} already answered in the cache); "
            f"estimated cost ${res.est_cost_usd:.4f}. Nothing was sent."
        )
        if res.candidates - res.reused:
            out.detail("the first request body is in user/cache/dry-run-example.json")
        return 0
    labels = ", ".join(f"{k} {res.labels.get(k, 0)}" for k in ("apply", "consider", "skip"))
    out.info(
        f"classified: {res.asked} asked, {res.reused} from cache, {res.failed} failed | {labels} | "
        f"cost ${res.cost_usd:.4f}"
    )
    if res.stopped:
        out.error(f"stopped early: {res.stopped}")
        return 4
    return 1 if res.failed else 0


def _cmd_run(args: argparse.Namespace) -> int:
    """The scheduled pipeline: ingest -> classify -> (report, next milestone)."""
    code = _cmd_ingest(args)
    if code:
        return code
    return _cmd_classify(args)


def _add_classify_args(s: argparse.ArgumentParser) -> None:
    s.add_argument("--limit", type=int, help="classify at most this many postings (newest first)")
    s.add_argument("--dry-run", action="store_true", help="show what would be sent and its cost; send nothing")
    s.add_argument("--max-cost", type=float, default=1.0,
                   help="refuse to start if the estimated cost is above this many dollars (default 1.00)")


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

    s = sub.add_parser("classify", help="classify kept postings that have no current result")
    _add_classify_args(s)
    s.set_defaults(func=_cmd_classify)

    s = sub.add_parser("run", help="the full pipeline: ingest, then classify")
    _add_classify_args(s)
    s.set_defaults(func=_cmd_run)

    s = sub.add_parser("privacy-check", help="fail if personal data, databases or keys are tracked")
    s.add_argument("repo", nargs="?", default=Path("."), type=Path)
    s.set_defaults(func=_cmd_privacy)

    s = sub.add_parser("make-private", help="turn this clone into a private copy (run only in a private repo)")
    s.add_argument("repo", nargs="?", default=Path("."), type=Path)
    s.set_defaults(func=_cmd_make_private)
    return p


def main(argv: list[str] | None = None) -> int:
    from jobmatcher import dotenv

    dotenv.load(".env")
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
