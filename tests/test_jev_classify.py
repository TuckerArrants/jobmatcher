"""The Jev classifier, decision rules and classify runner, against a fake API (no network, no key)."""

import hashlib
import json
from datetime import timedelta

import pytest

from jobmatcher.classifiers.jev import (
    REV, JevClassifier, JevClient, JevError, api_questions, flatten, load_question_set,
    parse_question_set, request_body,
)
from jobmatcher.classify import CostLimit, classify, profile_hash
from jobmatcher.config import Paths, package_yaml
from jobmatcher.decide import make_decider
from jobmatcher.models import Posting
from jobmatcher.store import Store

from conftest import NOW

QSET = load_question_set("jobs_v1")
DECIDE = make_decider(package_yaml("decision.default.yaml"))


def _u(*parts) -> float:
    return int(hashlib.md5("|".join(parts).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


class FakeJev:
    """Answers shaped like the real API. Values depend on the posting text, or are forced."""

    def __init__(self, model="jev-1.13.0", forced=None, fail_on=(), tokens=1500):
        self.model, self.forced, self.fail_on, self.tokens = model, forced or {}, fail_on, tokens
        self.bodies = []

    def system_one(self, body):
        self.bodies.append(body)
        text = body["state"]["posting"]
        if any(f in text for f in self.fail_on):
            raise JevError("POST /v1/systemone -> HTTP 422: bad")
        ans = {}
        for qid, q in body["questions"].items():
            base = qid.replace(REV, "")
            if base in self.forced:
                ans[qid] = self.forced[base](q, qid)
            elif q["type"] == "noul":
                ans[qid] = {"type": "noul", "noul": _u(text, base) * 0.5}
            elif q["type"] == "score":
                n = len(q["criteria"])
                ans[qid] = {"type": "score", "score": _u(text, base) * (n - 1), "confidence": 0.5}
            else:
                labels = list(q["criteria"])
                p = [_u(text, base, k) + 0.01 for k in labels]
                s = sum(p)
                ans[qid] = {"type": "choice", "confidence": 0.6,
                            "probabilities": {k: v / s for k, v in zip(labels, p)}}
        return {"model": self.model, "answers": ans, "usage": {"input_tokens": self.tokens, "output_tokens": 0}}


def choice(winner, conf=0.9, first_bias=False):
    """A forced choice answer; with first_bias, whichever option is listed first wins instead."""
    def answer(q, qid):
        labels = list(q["criteria"])
        top = labels[0] if first_bias else winner
        return {"type": "choice", "confidence": conf,
                "probabilities": {k: (0.9 if k == top else 0.1 / (len(labels) - 1)) for k in labels}}
    return answer


def score(level):
    return lambda q, qid: {"type": "score", "score": float(level), "confidence": 0.8}


def noul(p):
    return lambda q, qid: {"type": "noul", "noul": p}


def posting(i=0, title="Data Scientist"):
    return Posting(source="greenhouse", company="Co", title=f"{title} {i}", url=f"https://x.com/{i}",
                   locations=["Remote - US"], workplace="remote", text=f"Build models {i}.",
                   posted_at=NOW - timedelta(days=i))


# ------------------------------------------------------------------ question set

def test_question_set_is_valid_and_hash_tracks_content():
    assert QSET.model == "jev-1.13.0" and QSET.state_keys == ("posting", "profile")
    assert QSET.classifier_name.startswith("jev:jobs_v1#") and QSET.classifier_name.endswith("@jev-1.13.0")
    raw = package_yaml("questions/jobs_v1.yaml")
    raw["questions"]["verdict"]["instructions"] += " "
    assert parse_question_set(raw).hash != QSET.hash


@pytest.mark.parametrize("bad", [
    {"model": "jev-latest"},
    {"questions": {"x": {"type": "score", "instructions": "i", "criteria": ["only one"]}}},
    {"questions": {"x": {"type": "choice", "instructions": "i", "criteria": ["not", "a", "map"]}}},
    {"questions": {"x__rev": {"type": "noul", "instructions": "i"}}},
])
def test_question_set_validation(bad):
    raw = {"name": "t", "model": "jev-1.13.0",
           "questions": {"q": {"type": "noul", "instructions": "i"}}, **bad}
    with pytest.raises(ValueError):
        parse_question_set(raw)


def test_choices_are_also_sent_reversed_and_state_has_both_parts():
    body = request_body(QSET, posting(), "PROFILE TEXT")
    for c in QSET.ids("choice"):
        assert list(body["questions"][c + REV]["criteria"]) == list(reversed(list(QSET.questions[c]["criteria"])))
    assert set(body["state"]) == {"posting", "profile"}
    assert body["state"]["profile"] == "PROFILE TEXT" and "Title: Data Scientist" in body["state"]["posting"]
    assert len(body["questions"]) == len(QSET.questions) + len(QSET.ids("choice"))
    assert body["model"] == "jev-1.13.0"


def test_flatten_averages_orderings_and_flags_order_bias():
    # A model that always picks the first-listed option: forward says "below", reversed says "above".
    fake = FakeJev(forced={"level_fit": choice(None, first_bias=True)})
    sub = flatten(fake.system_one(request_body(QSET, posting(), "p"))["answers"], QSET)
    assert sub["level_fit__order_agree"] is False
    assert abs(sub["level_fit__p_below"] - sub["level_fit__p_above"]) < 1e-9
    fake = FakeJev(forced={"level_fit": choice("fits")})
    sub = flatten(fake.system_one(request_body(QSET, posting(), "p"))["answers"], QSET)
    assert sub["level_fit"] == "fits" and sub["level_fit__order_agree"] is True
    assert abs(sum(sub[f"level_fit__p_{k}"] for k in QSET.questions["level_fit"]["criteria"]) - 1) < 1e-9


def test_score_is_scaled_to_unit_interval():
    fake = FakeJev(forced={"role_match": score(3), "interest": score(0)})
    sub = flatten(fake.system_one(request_body(QSET, posting(), "p"))["answers"], QSET)
    assert sub["role_match"] == 1.0 and sub["interest"] == 0.0


# ------------------------------------------------------------------ decision rules

def decide_with(**forced):
    fake = FakeJev(forced=forced)
    sub = flatten(fake.system_one(request_body(QSET, posting(), "p"))["answers"], QSET)
    return DECIDE(sub)


def test_rules_in_order():
    strong = dict(verdict=choice("apply"), role_match=score(3), level_fit=choice("fits"),
                  hard_blocker=noul(0.1), unwanted=noul(0.1))
    assert decide_with(**strong)[:2] == ("apply", "verdict_apply")
    assert decide_with(**{**strong, "hard_blocker": noul(0.9)})[:2] == ("skip", "hard_blocker")
    assert decide_with(**{**strong, "unwanted": noul(0.8)})[:2] == ("skip", "unwanted")
    assert decide_with(**{**strong, "level_fit": choice("above")})[:2] == ("skip", "level_above")
    # apply verdict but the job only partly matches a target role -> not apply
    assert decide_with(**{**strong, "role_match": score(1)})[0] == "consider"
    assert decide_with(**{**strong, "verdict": choice("skip")})[:2] == ("skip", "verdict_skip")
    assert decide_with(**{**strong, "verdict": choice("consider")})[0] == "consider"


def test_order_disagreement_never_guesses():
    label, rule, *_ = decide_with(verdict=choice(None, first_bias=True), role_match=score(3),
                                  hard_blocker=noul(0.1), unwanted=noul(0.1), level_fit=choice("fits"))
    assert label == "consider" and rule.startswith("orderings_disagree") and "verdict" in rule
    # an untrusted "above" does not skip
    label, *_ = decide_with(level_fit=choice(None, first_bias=True), verdict=choice("consider"),
                            hard_blocker=noul(0.1), unwanted=noul(0.1))
    assert label == "consider"


def test_sort_score_ranks_better_matches_higher():
    hi = decide_with(role_match=score(3), interest=score(3), verdict=choice("apply"))[2]
    lo = decide_with(role_match=score(0), interest=score(1), verdict=choice("skip"))[2]
    assert 0 <= lo < hi <= 1


# ------------------------------------------------------------------ client

class _Resp:
    def __init__(self, status, data=None, headers=None):
        self.status_code, self._data, self.headers, self.text = status, data, headers or {}, json.dumps(data)

    def json(self):
        return self._data


class _Session:
    def __init__(self, responses):
        self.responses, self.seen_headers = list(responses), []

    def post(self, url, headers=None, json=None, timeout=None):
        self.seen_headers.append(headers)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


SECRET = "ts_" + "k" * 24  # built at runtime; never a literal key in the repo


def test_client_retries_and_never_exposes_key():
    sleeps = []
    s = _Session([TimeoutError(), _Resp(529, headers={"Retry-After": "2"}), _Resp(200, {"ok": 1})])
    c = JevClient(SECRET, session=s, sleep=sleeps.append)
    assert c.system_one({}) == {"ok": 1}
    assert sleeps[1] == 2.0 and len(sleeps) == 2
    assert s.seen_headers[0]["Authorization"] == f"Bearer {SECRET}"
    assert SECRET not in repr(c)
    with pytest.raises(JevError) as exc:
        JevClient(SECRET, session=_Session([_Resp(401, {"error": "bad key"})]), sleep=lambda s: None).system_one({})
    assert SECRET not in str(exc.value) and "refused the key" in str(exc.value)
    with pytest.raises(JevError):
        JevClient("")


def test_wrong_model_version_stops():
    clf = JevClassifier(QSET, FakeJev(model="jev-1.14.0"), DECIDE)
    with pytest.raises(JevError, match="answered"):
        clf.classify(posting(), "p")


# ------------------------------------------------------------------ runner

@pytest.fixture
def setup(tmp_path):
    paths = Paths(tmp_path)
    (tmp_path / "profile.md").write_text("Target roles: data scientist.")
    store = Store(paths.db)
    ps = [posting(i) for i in range(5)]
    store.upsert_postings(ps)
    store.set_prefilter({p.id: "kept" for p in ps[:4]} | {ps[4].id: "location"})
    return paths, store, ps


def test_classify_runs_once_per_posting_and_caches(setup):
    paths, store, ps = setup
    fake = FakeJev()
    clf = JevClassifier(QSET, fake, DECIDE)
    res = classify(paths, store, classifier=clf, workers=2)
    assert (res.candidates, res.asked, res.failed) == (4, 4, 0)  # the dropped posting is not asked
    assert sum(res.labels.values()) == 4
    assert res.cost_usd == pytest.approx(4 * 1500 / 1e6 * 0.042)
    again = classify(paths, store, classifier=clf)
    assert again.candidates == 0 and len(fake.bodies) == 4

    # A fresh database re-imports the JSONL cache instead of paying again.
    store2 = Store(paths.user / "data" / "other.sqlite")
    store2.upsert_postings(ps[:4])
    store2.set_prefilter({p.id: "kept" for p in ps[:4]})
    res3 = classify(paths, store2, classifier=clf)
    assert res3.reused == 4 and res3.asked == 0 and len(fake.bodies) == 4
    cache_text = next(paths.cache.glob("jev_*.jsonl")).read_text()
    assert "Build models" not in cache_text and "data scientist" not in cache_text  # no posting/profile text


def test_editing_the_profile_reclassifies(setup):
    paths, store, _ = setup
    fake = FakeJev()
    clf = JevClassifier(QSET, fake, DECIDE)
    classify(paths, store, classifier=clf)
    (paths.user / "profile.md").write_text("Target roles: quantitative researcher.")
    res = classify(paths, store, classifier=clf)
    assert res.asked == 4 and len(fake.bodies) == 8


def test_limit_takes_newest_first(setup):
    paths, store, ps = setup
    fake = FakeJev()
    classify(paths, store, classifier=JevClassifier(QSET, fake, DECIDE), limit=2)
    sent = {b["state"]["posting"].splitlines()[0] for b in fake.bodies}
    assert sent == {f"Title: {ps[0].title}", f"Title: {ps[1].title}"}


def test_dry_run_sends_nothing_and_needs_no_key(setup, monkeypatch):
    paths, store, _ = setup
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    res = classify(paths, store, dry_run=True)
    assert res.dry_run and res.candidates == 4 and res.asked == 0
    assert 0 < res.est_cost_usd < 0.01
    example = json.loads((paths.cache / "dry-run-example.json").read_text())
    assert set(example) == {"model", "state", "questions"}


def test_cost_guard(setup):
    paths, store, _ = setup
    fake = FakeJev()
    with pytest.raises(CostLimit):
        classify(paths, store, classifier=JevClassifier(QSET, fake, DECIDE), max_cost=0.0000001)
    assert fake.bodies == []


def test_failures_are_counted_and_model_mismatch_stops(setup):
    paths, store, ps = setup
    res = classify(paths, store, classifier=JevClassifier(QSET, FakeJev(fail_on=["models 1."]), DECIDE))
    assert res.failed == 1 and res.asked == 3
    res = classify(paths, store, classifier=JevClassifier(QSET, FakeJev(model="jev-1.14.0"), DECIDE), workers=1)
    assert res.asked == 0 and res.stopped and "answered" in res.stopped


def test_profile_hash_changes_with_text():
    assert profile_hash("a") != profile_hash("b") and len(profile_hash("a")) == 12
