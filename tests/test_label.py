"""The labeling page: queue order, labels.csv, undo, and the local web server."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from jobmatcher.config import Paths
from jobmatcher.label import Session, build_queue, read_labels, serve
from jobmatcher.models import Posting
from jobmatcher.store import Store

from conftest import NOW


@pytest.fixture
def session(tmp_path):
    paths = Paths(tmp_path)
    store = Store(paths.db)
    labels = ["apply"] * 2 + ["consider"] * 4 + ["skip"] * 10
    for i, lab in enumerate(labels):
        p = Posting(source="greenhouse", company=f"Co{i}", title=f"Job {i}", url=f"https://x/{i}",
                    text=f"Body {i}", posted_at=NOW, locations=["Remote - US"], workplace="remote")
        store.upsert_postings([p])
        store.save_result(p.id, "clf", lab, {"rule": "r", "sub_scores": {"role_match": 0.9}}, {}, 0)
    # one posting that failed the prefilter is never shown
    store.set_prefilter({r["id"]: "kept" for r in store.db.execute("SELECT id FROM postings")})
    store.db.execute("UPDATE postings SET prefilter = 'location' WHERE title = 'Job 15'")
    store.db.commit()
    return Session(paths, store, "clf", target=10)


def test_queue_interleaves_strata_deterministically(session):
    q = build_queue(session.store, "clf")
    assert len(q) == 15
    assert [s for _, s in q[:6]] == ["apply", "consider", "skip", "apply", "consider", "skip"]
    assert q == build_queue(session.store, "clf")  # same order every time


def test_label_writes_csv_and_db_and_undo(session):
    first = session.next_id()
    session.label(first, "apply", "  great fit  ")
    rows = read_labels(session.paths.labels)
    assert rows[first]["label"] == "apply" and rows[first]["note"] == "great fit"
    assert rows[first]["stratum"] == "apply" and rows[first]["classifier"] == "clf"
    assert session.store.labels() == {first: "apply"}
    assert session.next_id() != first
    assert session.undo() == first
    assert read_labels(session.paths.labels) == {} and session.store.labels() == {}
    assert session.next_id() == first
    with pytest.raises(ValueError):
        session.label(first, "maybe")


def test_labels_survive_a_restart(session):
    pid = session.next_id()
    session.label(pid, "skip")
    again = Session(session.paths, session.store, "clf")
    assert again.stats()["labeled"] == 1 and again.next_id() != pid


def test_posting_payload_hides_the_classifier(session):
    data = session.posting(session.next_id())
    assert data["title"].startswith("Job") and data["text"].startswith("Body")
    assert not ({"rule", "sub_scores", "decision", "stratum"} & set(data))
    assert data["label"] is None


@pytest.fixture
def server(session):
    srv = serve(session, open_browser=False)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", session
    srv.shutdown()
    srv.server_close()


def call(url, body=None, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read().decode()


def test_server_round_trip(server):
    base, session = server
    status, page = call(base + "/")
    assert status == 200 and "<title>Label jobs</title>" in page
    _, body = call(base + "/api/next")
    d = json.loads(body)
    pid = d["posting"]["id"]
    assert d["stats"]["labeled"] == 0 and d["stats"]["available"] == 15
    call(base + "/api/label", {"id": pid, "label": "consider", "note": "maybe"})
    assert session.stats()["counts"]["consider"] == 1
    _, body = call(base + "/api/undo", {})
    assert json.loads(body)["id"] == pid and session.stats()["labeled"] == 0
    with pytest.raises(urllib.error.HTTPError) as exc:
        call(base + "/api/label", {"id": pid, "label": "nope"})
    assert exc.value.code == 400


def test_server_refuses_cross_origin_posts(server):
    base, session = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        call(base + "/api/label", {"id": session.next_id(), "label": "apply"},
             headers={"Origin": "https://evil.example"})
    assert exc.value.code == 403 and session.stats()["labeled"] == 0


def test_server_binds_to_localhost_only(server):
    base, _ = server
    assert base.startswith("http://127.0.0.1:")
