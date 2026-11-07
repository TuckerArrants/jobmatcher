"""Classify kept postings that have no current result, with a cache, a dry run and a cost guard.

A result is current when it was made by the same classifier (question set + model) for the same
profile text. Editing user/profile.md makes old results stale, so those postings are asked again.

Answers are stored in the SQLite database and also appended to user/cache/<classifier>.jsonl
(probabilities only, no posting or profile text). That file can be committed as a backup; a
fresh database re-imports it, so nothing is ever paid for twice.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from jobmatcher.classifiers.jev import (
    PRICE_PER_MTOK, JevClassifier, JevClient, JevError, estimate_tokens, load_question_set, request_body,
)
from jobmatcher.config import Paths, load_profile
from jobmatcher.decide import load_decision_config, make_decider
from jobmatcher.models import Posting
from jobmatcher.store import Store, now_iso


class CostLimit(Exception):
    pass


def profile_hash(profile: str) -> str:
    return hashlib.sha256(profile.encode("utf-8")).hexdigest()[:12]


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


@dataclass
class ClassifyResult:
    candidates: int = 0
    asked: int = 0
    reused: int = 0
    failed: int = 0
    labels: Counter = field(default_factory=Counter)
    cost_usd: float = 0.0
    est_cost_usd: float = 0.0
    dry_run: bool = False
    stopped: str | None = None


def build_classifier(paths: Paths, client: JevClient | None = None, offline: bool = False) -> JevClassifier:
    """The configured classifier. ``offline`` builds it without a client (dry runs, re-deciding)."""
    cfg = load_decision_config(paths)
    qset = load_question_set(cfg.get("question_set", "jobs_v1"))
    if client is None and not offline:
        client = JevClient(os.environ.get("TYPESAFE_API_KEY", ""))
    return JevClassifier(qset, client, make_decider(cfg))


class Cache:
    """Append-only JSONL of raw answers, keyed by posting id, classifier and profile hash."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> dict[tuple[str, str], dict]:
        out = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    out[(rec["id"], rec["profile_hash"])] = rec["response"]
        return out

    def append(self, posting_id: str, classifier: str, phash: str, response: dict) -> None:
        rec = {"id": posting_id, "classifier": classifier, "profile_hash": phash,
               "response": response, "ts": now_iso()}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")


def candidates(store: Store, classifier_name: str, phash: str, limit: int | None) -> list[Posting]:
    current = {
        r["posting_id"]
        for r in store.results(classifier_name)
        if json.loads(r["decision"]).get("profile_hash") == phash
    }
    kept = [p for p in store.postings("prefilter = 'kept'") if p.id not in current]
    kept.sort(key=lambda p: (p.posted_at is None, -(p.posted_at.timestamp() if p.posted_at else 0)))
    return kept[:limit] if limit else kept


def _save(store: Store, clf: JevClassifier, p: Posting, response: dict, phash: str) -> str:
    d = clf.decision_from(response)
    decision = {
        "rule": d.rule, "sort_score": d.sort_score, "confidence": d.confidence,
        "sub_scores": d.sub_scores, "profile_hash": phash, "model": response.get("model"),
    }
    store.save_result(p.id, clf.name, d.label, decision, response, d.cost_usd)
    return d.label


def classify(
    paths: Paths,
    store: Store,
    *,
    classifier: JevClassifier | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    max_cost: float = 1.0,
    workers: int = 4,
) -> ClassifyResult:
    profile = load_profile(paths)
    phash = profile_hash(profile)
    if classifier is None:
        classifier = build_classifier(paths, offline=dry_run)
    res = ClassifyResult(dry_run=dry_run)
    todo = candidates(store, classifier.name, phash, limit)
    res.candidates = len(todo)

    # Reuse answers already paid for (e.g. after moving to a new machine).
    cache = Cache(paths.cache / f"{_safe(classifier.name)}.jsonl")
    cached = cache.load()
    remaining = []
    for p in todo:
        response = cached.get((p.id, phash))
        if response is not None:
            res.labels[_save(store, classifier, p, response, phash)] += 1
            res.reused += 1
        else:
            remaining.append(p)

    bodies = {p.id: request_body(classifier.qset, p, profile) for p in remaining}
    res.est_cost_usd = sum(estimate_tokens(b) for b in bodies.values()) / 1e6 * PRICE_PER_MTOK
    if dry_run:
        if remaining:
            example = paths.cache / "dry-run-example.json"
            example.parent.mkdir(parents=True, exist_ok=True)
            example.write_text(json.dumps(bodies[remaining[0].id], indent=2, ensure_ascii=False), encoding="utf-8")
        return res
    if res.est_cost_usd > max_cost:
        raise CostLimit(
            f"estimated ${res.est_cost_usd:.2f} for {len(remaining)} postings is above the "
            f"--max-cost limit of ${max_cost:.2f}; raise the limit or use --limit"
        )

    by_id = {p.id: p for p in remaining}
    stop = threading.Event()

    def one(pid: str) -> dict | None:
        if stop.is_set():
            return None
        return classifier.ask(by_id[pid], profile)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(one, pid): pid for pid in by_id}
        for fut in as_completed(futures):
            pid = futures[fut]
            try:
                response = fut.result()
            except JevError as exc:
                msg = str(exc)
                if "answered" in msg or "refused the key" in msg or "not set" in msg:
                    stop.set()
                    res.stopped = msg
                res.failed += 1
                continue
            if response is None:
                continue
            cache.append(pid, classifier.name, phash, response)
            res.labels[_save(store, classifier, by_id[pid], response, phash)] += 1
            res.asked += 1
            res.cost_usd += float((response.get("usage") or {}).get("input_tokens", 0)) / 1e6 * PRICE_PER_MTOK

    store.record_run(
        "classify",
        {"candidates": res.candidates, "asked": res.asked, "reused": res.reused, "failed": res.failed,
         **{f"label:{k}": v for k, v in res.labels.items()}},
        res.cost_usd,
    )
    return res
