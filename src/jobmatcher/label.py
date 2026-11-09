"""Label postings in the browser: ``jobmatch label``.

A tiny local web page (127.0.0.1 only, nothing leaves the machine) shows one posting at a time.
You choose apply / consider / skip (keys A / C / S) and can add a note. The classifier's answer
is never shown, so it cannot sway you.

Labels are written to user/labels.csv (the source of truth, worth committing as a backup) and
mirrored into the database.

Order: postings are grouped by the classifier's label (apply, consider, skip), each group is
shuffled with a fixed seed, and the groups are interleaved. This puts enough of the rare
"apply" predictions in front of you to measure precision, while still sampling skips to measure
what the classifier misses. Each label records the group it was drawn from, so evaluation can
weight the groups correctly.
"""

from __future__ import annotations

import csv
import json
import random
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from jobmatcher.classifiers.base import LABELS
from jobmatcher.config import Paths
from jobmatcher.store import Store

FIELDS = ["posting_id", "label", "labeled_at", "note", "stratum", "classifier"]
SEED = 20261107


# ------------------------------------------------------------------ labels.csv

def read_labels(path: Path) -> dict[str, dict]:
    """Latest row per posting (the file is append-only; later rows win)."""
    out: dict[str, dict] = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("label") in LABELS or row.get("label") == "":
                    out[row["posting_id"]] = row
    return {k: v for k, v in out.items() if v.get("label")}


def append_label(path: Path, row: dict) -> None:
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


# ------------------------------------------------------------------ queue

def build_queue(store: Store, classifier: str, seed: int = SEED) -> list[tuple[str, str]]:
    """[(posting_id, stratum)] for every classified posting that passed the prefilter."""
    groups: dict[str, list[str]] = {k: [] for k in LABELS}
    q = ("SELECT r.posting_id, r.label FROM results r JOIN postings p ON p.id = r.posting_id "
         "WHERE r.classifier = ? AND p.prefilter = 'kept' ORDER BY r.posting_id")
    for row in store.db.execute(q, (classifier,)):
        groups.setdefault(row["label"] or "skip", []).append(row["posting_id"])
    rng = random.Random(seed)
    for ids in groups.values():
        rng.shuffle(ids)
    queue, i = [], 0
    while any(i < len(ids) for ids in groups.values()):
        for label in LABELS:
            if i < len(groups[label]):
                queue.append((groups[label][i], label))
        i += 1
    return queue


# ------------------------------------------------------------------ session

class Session:
    def __init__(self, paths: Paths, store: Store, classifier: str, target: int = 150) -> None:
        self.paths, self.store, self.classifier, self.target = paths, store, classifier, target
        self.lock = threading.Lock()
        self.queue = build_queue(store, classifier)
        self.strata = dict(self.queue)
        self.labels = read_labels(paths.labels)
        for pid, row in self.labels.items():  # mirror into the database
            if store.get(pid):
                store.set_label(pid, row["label"], row.get("note") or None)
        self.history: list[str] = []

    def next_id(self) -> str | None:
        for pid, _ in self.queue:
            if pid not in self.labels:
                return pid
        return None

    def posting(self, pid: str) -> dict | None:
        p = self.store.get(pid)
        if not p:
            return None
        salary = ""
        if p.salary_min or p.salary_max:
            lo, hi = p.salary_min or p.salary_max, p.salary_max or p.salary_min
            salary = f"${lo / 1000:,.0f}k" + (f"–${hi / 1000:,.0f}k" if hi != lo else "")
        return {
            "id": p.id, "title": p.title, "company": p.company, "url": p.url,
            "where": ", ".join(x for x in (p.location, p.workplace or "") if x),
            "salary": salary, "posted": p.posted_at.strftime("%b %d, %Y") if p.posted_at else "",
            "source": p.source, "text": p.text,
            "label": (self.labels.get(pid) or {}).get("label"),
            "note": (self.labels.get(pid) or {}).get("note", ""),
        }

    def stats(self) -> dict:
        counts = {k: 0 for k in LABELS}
        for row in self.labels.values():
            counts[row["label"]] += 1
        return {"labeled": len(self.labels), "target": self.target, "available": len(self.queue),
                "counts": counts}

    def label(self, pid: str, label: str, note: str = "") -> None:
        if label not in LABELS:
            raise ValueError("label must be apply, consider or skip")
        if pid not in self.strata:
            raise KeyError(pid)
        row = {"posting_id": pid, "label": label,
               "labeled_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
               "note": note.strip(), "stratum": self.strata[pid], "classifier": self.classifier}
        with self.lock:
            append_label(self.paths.labels, row)
            self.labels[pid] = row
            self.store.set_label(pid, label, row["note"] or None)
            self.history.append(pid)

    def undo(self) -> str | None:
        """Forget the most recent label of this session; returns that posting's id."""
        with self.lock:
            if not self.history:
                return None
            pid = self.history.pop()
            append_label(self.paths.labels, {"posting_id": pid, "label": "",
                                             "labeled_at": datetime.now(timezone.utc).isoformat()})
            self.labels.pop(pid, None)
            self.store.db.execute("DELETE FROM labels WHERE posting_id = ?", (pid,))
            self.store.db.commit()
            return pid


# ------------------------------------------------------------------ web page

PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Label jobs</title><style>
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a64;--line:#e4e1d8;--card:#fff;--link:#1b5fa8;
--apply:#1f7a4d;--consider:#a86a00;--skip:#77766f}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--muted:#a3a29b;--line:#2f2e2a;
--card:#1f1e1b;--link:#7db3ef;--apply:#3fa673;--consider:#c98d1c;--skip:#77766f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:860px;margin:0 auto;padding:20px 16px 140px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;color:var(--muted);font-size:14px}
.bar{height:6px;background:var(--line);border-radius:3px;margin:8px 0 18px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--apply);width:0}
h1{font-size:22px;margin:0}a{color:var(--link)}.meta{color:var(--muted);margin:4px 0 14px}
.text{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;
white-space:pre-wrap;max-height:55vh;overflow:auto}
.dock{position:fixed;left:0;right:0;bottom:0;background:var(--bg);border-top:1px solid var(--line)}
.dock div{max-width:860px;margin:0 auto;padding:12px 16px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
button{font:inherit;font-weight:600;border:0;border-radius:8px;padding:10px 18px;color:#fff;cursor:pointer}
.b-apply{background:var(--apply)}.b-consider{background:var(--consider)}.b-skip{background:var(--skip)}
.b-ghost{background:transparent;color:var(--muted);border:1px solid var(--line)}
input{flex:1;min-width:180px;font:inherit;padding:9px 10px;border-radius:8px;border:1px solid var(--line);
background:var(--card);color:var(--fg)}kbd{font-size:12px;opacity:.8;margin-left:6px}
.done{text-align:center;padding:60px 0}.flash{color:var(--muted);font-size:13px;min-height:20px}
</style></head><body><main>
<div class=top><span id=progress></span><span>Keys: <b>A</b> apply · <b>C</b> consider · <b>S</b> skip · <b>U</b> undo</span></div>
<div class=bar><i id=bar></i></div>
<div id=card></div><div class=flash id=flash></div></main>
<div class=dock><div>
<button class=b-apply onclick="send('apply')">Apply<kbd>A</kbd></button>
<button class=b-consider onclick="send('consider')">Consider<kbd>C</kbd></button>
<button class=b-skip onclick="send('skip')">Skip<kbd>S</kbd></button>
<input id=note placeholder="Optional note: why (Enter does not submit)">
<button class=b-ghost onclick="undo()">Undo<kbd>U</kbd></button>
</div></div>
<script>
let cur=null;
const $=id=>document.getElementById(id);
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
 if(!r.ok)throw new Error(await r.text());return r.json();}
function el(tag,text,cls){const e=document.createElement(tag);if(text)e.textContent=text;if(cls)e.className=cls;return e;}
async function load(id){
 const d=await api(id?'/api/posting?id='+encodeURIComponent(id):'/api/next');
 const s=d.stats;$('progress').textContent=`${s.labeled} labeled (goal ${s.target}) · apply ${s.counts.apply} · consider ${s.counts.consider} · skip ${s.counts.skip}`;
 $('bar').style.width=Math.min(100,100*s.labeled/s.target)+'%';
 const card=$('card');card.replaceChildren();cur=d.posting;$('note').value=cur&&cur.note||'';
 if(!cur){card.append(el('div','All available postings are labeled. You can close this tab.','done'));return;}
 const h=el('h1');const a=el('a',cur.title);a.href=cur.url;a.target='_blank';a.rel='noopener';h.append(a);
 const meta=[cur.company,cur.where,cur.salary,cur.posted&&('posted '+cur.posted),'via '+cur.source].filter(Boolean).join(' · ');
 card.append(h,el('div',meta,'meta'),el('div',cur.text||'(no description; open the link)','text'));
 $('card').querySelector('.text').scrollTop=0;
}
async function send(label){if(!cur)return;const t=cur.title;
 await api('/api/label',{id:cur.id,label,note:$('note').value});$('flash').textContent=`Saved: ${label} · ${t}`;load();}
async function undo(){const d=await api('/api/undo',{});if(d.id){$('flash').textContent='Undone';load(d.id);}}
document.addEventListener('keydown',e=>{if(e.target.tagName==='INPUT'||e.ctrlKey||e.metaKey||e.altKey)return;
 const k=e.key.toLowerCase();if(k==='a')send('apply');else if(k==='c')send('consider');else if(k==='s')send('skip');else if(k==='u')undo();});
load();
</script></body></html>"""


def make_handler(session: Session):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # no request logging: URLs carry posting ids
            pass

        def _send(self, code: int, body: str, ctype: str = "application/json") -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj))

        def do_GET(self) -> None:  # noqa: N802
            from urllib.parse import parse_qs, urlsplit

            url = urlsplit(self.path)
            if url.path == "/":
                return self._send(200, PAGE, "text/html")
            if url.path == "/api/next":
                pid = session.next_id()
                return self._json({"posting": session.posting(pid) if pid else None, "stats": session.stats()})
            if url.path == "/api/posting":
                pid = (parse_qs(url.query).get("id") or [""])[0]
                return self._json({"posting": session.posting(pid), "stats": session.stats()})
            self._send(404, "{}")

        def do_POST(self) -> None:  # noqa: N802
            # Only same-origin requests from the page itself.
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                return self._send(403, '{"error":"cross-origin"}')
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._send(400, '{"error":"bad json"}')
            if self.path == "/api/label":
                try:
                    session.label(str(body.get("id")), str(body.get("label")), str(body.get("note") or ""))
                except (ValueError, KeyError) as exc:
                    return self._json({"error": str(exc)}, 400)
                return self._json({"ok": True})
            if self.path == "/api/undo":
                return self._json({"id": session.undo()})
            self._send(404, "{}")

    return Handler


def serve(session: Session, port: int = 0, open_browser: bool = True) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), make_handler(session))
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{server.server_address[1]}/")
    return server
