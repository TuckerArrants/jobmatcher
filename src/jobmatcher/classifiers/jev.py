"""Jev (TypeSafe), the baseline classifier.

Jev is zero-shot and decision-only: one request sends a state and typed questions and returns
probabilities, never text. API: POST https://api.typesafe.ai/v1/systemone with a bearer key.

* noul:   a yes/no statement; answer {"noul": p_yes}
* choice: options with descriptions; answer {"probabilities": {...}, "confidence"}
* score:  2-10 ordered levels, lowest first; answer {"score": expected level index, "confidence"}

Design rules (from TypeSafe's docs and earlier use):
* the model version is pinned, and a response from any other version stops the run;
* every choice is asked twice, the second time with options reversed (Jev 1.13 favours the first
  option); the two probability vectors are averaged and their agreement kept as a quality flag;
* all questions go in one request, so the state is billed once;
* the key lives in memory only and is never printed, logged or put in an error message.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from jobmatcher.classifiers.base import Decision
from jobmatcher.models import Posting

API_URL = "https://api.typesafe.ai/v1/systemone"
PRICE_PER_MTOK = 0.042  # USD per million input tokens (jev-1.13); output is free
REV = "__rev"
CHARS_PER_TOKEN = 4.0  # rough, for dry-run estimates only


class JevError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- question sets

@dataclass(frozen=True)
class QuestionSet:
    name: str
    model: str
    state_keys: tuple[str, ...]
    reverse_choices: bool
    questions: dict[str, dict[str, Any]]

    @property
    def hash(self) -> str:
        payload = {
            "model": self.model, "state_keys": list(self.state_keys),
            "reverse_choices": self.reverse_choices, "questions": self.questions,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    @property
    def classifier_name(self) -> str:
        return f"jev:{self.name}#{self.hash[:8]}@{self.model}"

    def ids(self, kind: str | None = None) -> list[str]:
        return [q for q, d in self.questions.items() if kind is None or d["type"] == kind]


def parse_question_set(raw: dict) -> QuestionSet:
    qs: dict[str, dict] = {}
    for qid, d in (raw.get("questions") or {}).items():
        d = dict(d)
        t = d.get("type")
        if REV in qid:
            raise ValueError(f"{qid}: question ids may not contain {REV!r}")
        if t == "noul":
            pass
        elif t == "choice":
            if not isinstance(d.get("criteria"), dict) or not 1 <= len(d["criteria"]) <= 255:
                raise ValueError(f"{qid}: a choice needs a mapping of 1-255 options to descriptions")
        elif t == "score":
            if not isinstance(d.get("criteria"), list) or not 2 <= len(d["criteria"]) <= 10:
                raise ValueError(f"{qid}: a score needs a list of 2-10 level descriptions, lowest first")
        else:
            raise ValueError(f"{qid}: unsupported type {t!r}")
        if not d.get("instructions"):
            raise ValueError(f"{qid}: missing instructions")
        qs[qid] = d
    if not qs:
        raise ValueError("question set has no questions")
    model = str(raw["model"])
    if model.endswith(("latest", "preview")):
        raise ValueError("pin an exact model version (e.g. jev-1.13.0), not an alias")
    return QuestionSet(
        name=str(raw["name"]), model=model,
        state_keys=tuple(raw.get("state_keys") or ("posting", "profile")),
        reverse_choices=bool(raw.get("reverse_choices", True)), questions=qs,
    )


def load_question_set(name_or_path: str) -> QuestionSet:
    """A bundled set by name (``jobs_v1``) or a path to a YAML file."""
    path = Path(name_or_path)
    if path.suffix in (".yaml", ".yml") and path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = resources.files("jobmatcher").joinpath("config", "questions", f"{name_or_path}.yaml").read_text(
            encoding="utf-8"
        )
    return parse_question_set(yaml.safe_load(text))


def api_questions(qset: QuestionSet) -> dict[str, dict]:
    """The questions as sent: each choice also asked with its options in reverse order."""
    out = {}
    for qid, d in qset.questions.items():
        out[qid] = d
        if d["type"] == "choice" and qset.reverse_choices:
            out[qid + REV] = {**d, "criteria": dict(reversed(list(d["criteria"].items())))}
    return out


def posting_state_text(p: Posting) -> str:
    head = [f"Title: {p.title}", f"Company: {p.company}"]
    if p.locations:
        head.append(f"Location: {p.location}")
    if p.workplace:
        head.append(f"Workplace: {p.workplace}")
    return "\n".join(head) + "\n\n" + (p.text or "")


def request_body(qset: QuestionSet, posting: Posting, profile: str) -> dict:
    values = {"posting": posting_state_text(posting), "profile": profile}
    missing = [k for k in qset.state_keys if k not in values]
    if missing:
        raise ValueError(f"question set asks for unknown state keys {missing}")
    return {
        "model": qset.model,
        "state": {k: values[k] for k in qset.state_keys},
        "questions": api_questions(qset),
    }


def estimate_tokens(body: dict) -> int:
    return int(len(json.dumps(body, ensure_ascii=False)) / CHARS_PER_TOKEN)


# ----------------------------------------------------------------------------- client

class JevClient:
    """Minimal HTTP client with retries. The key is held in memory and never printed."""

    RETRY_STATUS = {429, 500, 502, 503, 504, 529}

    def __init__(self, api_key: str, *, timeout: float = 90.0, max_retries: int = 6,
                 session=None, sleep=time.sleep) -> None:
        if not api_key:
            raise JevError("TYPESAFE_API_KEY is not set (put it in .env, or in the environment)")
        import requests

        self._key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self._sleep = sleep

    def __repr__(self) -> str:  # never show the key
        return "JevClient(<key hidden>)"

    def system_one(self, body: dict) -> dict:
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                r = self.session.post(API_URL, headers=headers, json=body, timeout=self.timeout)
            except Exception as exc:  # noqa: BLE001  timeouts, connection resets
                if attempt == self.max_retries:
                    raise JevError(f"request failed after {attempt + 1} attempts: {type(exc).__name__}") from None
                self._sleep(delay + random.random() * delay)
                delay = min(delay * 2, 60)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in self.RETRY_STATUS and attempt < self.max_retries:
                ra = str(r.headers.get("Retry-After", ""))
                wait = float(ra) if ra.replace(".", "", 1).isdigit() else delay + random.random() * delay
                self._sleep(min(wait, 120))
                delay = min(delay * 2, 60)
                continue
            if r.status_code in (401, 403):
                raise JevError(f"TypeSafe refused the key (HTTP {r.status_code}); check TYPESAFE_API_KEY")
            raise JevError(f"POST /v1/systemone -> HTTP {r.status_code}: {_short(r)}")
        raise JevError("unreachable")


def _short(r) -> str:
    try:
        return json.dumps(r.json())[:200]
    except Exception:  # noqa: BLE001
        return (getattr(r, "text", "") or "")[:200]


# ----------------------------------------------------------------------------- answers

def flatten(answers: dict, qset: QuestionSet) -> dict[str, Any]:
    """Raw answers -> sub-scores.

    noul  q             probability of yes
    score q             expected level scaled to 0..1, and q__conf
    choice q            argmax of the averaged probabilities, q__p_<option>, q__conf,
                        q__order_agree (forward and reversed argmax agree)
    """
    out: dict[str, Any] = {}
    for qid, d in qset.questions.items():
        a = answers[qid]
        t = d["type"]
        if t == "noul":
            out[qid] = float(a["noul"])
        elif t == "score":
            out[qid] = float(a["score"]) / (len(d["criteria"]) - 1)
            out[f"{qid}__conf"] = float(a.get("confidence", 0.0))
        else:
            labels = list(d["criteria"])
            p = [float(a["probabilities"].get(k, 0.0)) for k in labels]
            conf = float(a.get("confidence", 0.0))
            agree = True
            b = answers.get(qid + REV)
            if qset.reverse_choices and b is not None:
                p2 = [float(b["probabilities"].get(k, 0.0)) for k in labels]
                agree = _argmax(p) == _argmax(p2)
                p = [(x + y) / 2 for x, y in zip(p, p2)]
                conf = (conf + float(b.get("confidence", 0.0))) / 2
            total = sum(p)
            if total > 0:
                p = [x / total for x in p]
            out[qid] = labels[_argmax(p)]
            for k, v in zip(labels, p):
                out[f"{qid}__p_{k}"] = v
            out[f"{qid}__conf"] = conf
            out[f"{qid}__order_agree"] = agree
    return out


def _argmax(xs: list[float]) -> int:
    return max(range(len(xs)), key=xs.__getitem__)


def cost_usd(response: dict) -> float:
    return float((response.get("usage") or {}).get("input_tokens", 0)) / 1e6 * PRICE_PER_MTOK


# ----------------------------------------------------------------------------- classifier

class JevClassifier:
    def __init__(self, qset: QuestionSet, client: JevClient | None, decide) -> None:
        self.qset = qset
        self.client = client
        self.decide = decide  # sub_scores -> (label, rule, sort_score, confidence)
        self.name = qset.classifier_name

    def ask(self, posting: Posting, profile: str) -> dict:
        if self.client is None:
            raise JevError("TYPESAFE_API_KEY is not set (put it in .env, or in the environment)")
        response = self.client.system_one(request_body(self.qset, posting, profile))
        answered = response.get("model")
        if answered != self.qset.model:
            raise JevError(
                f"asked for {self.qset.model} but {answered!r} answered; stopping. "
                "Pin a served version in a new question set."
            )
        return response

    def decision_from(self, response: dict) -> Decision:
        sub = flatten(response["answers"], self.qset)
        label, rule, sort_score, confidence = self.decide(sub)
        return Decision(label=label, sort_score=sort_score, sub_scores=sub, confidence=confidence,
                        rule=rule, cost_usd=cost_usd(response), raw=response)

    def classify(self, posting: Posting, profile: str) -> Decision:
        return self.decision_from(self.ask(posting, profile))
