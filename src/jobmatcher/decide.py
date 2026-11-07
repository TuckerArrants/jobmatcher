"""Turn sub-scores into apply / consider / skip, in code, with the rule that decided it.

Rules and thresholds live in config/decision.default.yaml (override in user/decision.yaml).
When the classifier's two orderings of a choice disagree, that choice is not trusted, and the
posting falls through to "consider". Never guess.
"""

from __future__ import annotations

from typing import Any

from jobmatcher.config import Paths, load_layered


def load_decision_config(paths: Paths) -> dict:
    return load_layered("decision.default.yaml", paths.decision)


def _trusted(sub: dict, q: str, label: str) -> bool:
    return sub.get(q) == label and bool(sub.get(f"{q}__order_agree", True))


def make_decider(cfg: dict):
    weights: dict[str, float] = cfg.get("sort_weights") or {}

    def sort_score(sub: dict) -> float:
        total = sum(weights.values()) or 1.0
        return sum(float(sub.get(k, 0.0)) * w for k, w in weights.items()) / total

    def decide(sub: dict[str, Any]) -> tuple[str, str, float, float | None]:
        score = sort_score(sub)
        conf = sub.get("verdict__conf")
        if sub.get("hard_blocker", 0.0) >= cfg["hard_blocker_skip"]:
            return "skip", "hard_blocker", score, conf
        if sub.get("unwanted", 0.0) >= cfg["unwanted_skip"]:
            return "skip", "unwanted", score, conf
        if _trusted(sub, "level_fit", "above"):
            return "skip", "level_above", score, conf
        if (
            _trusted(sub, "verdict", "apply")
            and float(sub.get("verdict__conf", 0.0)) >= cfg["t_apply"]
            and float(sub.get("role_match", 0.0)) >= cfg["role_match_apply"]
        ):
            return "apply", "verdict_apply", score, conf
        if _trusted(sub, "verdict", "skip") and float(sub.get("verdict__conf", 0.0)) >= cfg["t_skip"]:
            return "skip", "verdict_skip", score, conf
        disagree = [q for q in ("verdict", "level_fit") if sub.get(f"{q}__order_agree") is False]
        return "consider", ("orderings_disagree:" + ",".join(disagree)) if disagree else "uncertain", score, conf

    return decide
