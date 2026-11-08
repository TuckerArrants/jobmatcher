"""The digest: user/reports/YYYY-MM-DD.md and .html, plus user/reports/latest.html.

Which postings: classified by the current classifier for the current profile, still passing the
prefilter, and still open (seen in the latest ingest). By default only postings that are new
since the previous report; ``show_all`` lists every open one. The first report lists everything.

Labels are re-decided here from the stored sub-scores with the current decision config, so
editing thresholds in user/decision.yaml takes effect without asking the classifier again.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jobmatcher.classify import build_classifier, profile_hash
from jobmatcher.config import Paths, load_profile, load_yaml
from jobmatcher.decide import load_decision_config, make_decider
from jobmatcher.models import Posting
from jobmatcher.store import Store, now_iso

RULE_TEXT = {
    "verdict_apply": "verdict apply, strong role match",
    "verdict_skip": "verdict skip",
    "hard_blocker": "requires a credential the profile lacks",
    "unwanted": "a kind of work listed as unwanted",
    "level_above": "far more senior than the profile",
    "uncertain": "mixed signals",
}
ROLE_LEVELS = ("unrelated", "partial", "close", "exact")
INTEREST_LEVELS = ("unwanted", "neutral", "related", "strong")


@dataclass
class Row:
    posting: Posting
    label: str
    rule: str
    sort_score: float
    sub: dict
    resume: str | None
    first_seen: str


@dataclass
class Digest:
    date: str
    rows: list[Row] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    since: str | None = None
    show_all: bool = False

    def by_label(self, label: str) -> list[Row]:
        return sorted((r for r in self.rows if r.label == label), key=lambda r: -r.sort_score)


# ------------------------------------------------------------------ building

def load_resumes(paths: Paths) -> dict:
    return load_yaml(paths.resumes) or {}


def suggest_resume(p: Posting, resumes: dict) -> str | None:
    """First version whose keywords appear in the title, then the first in the text, else default."""
    versions = resumes.get("versions") or []
    for field_text in (p.title.lower(), p.text.lower()):
        for v in versions:
            if any(str(k).lower() in field_text for k in v.get("keywords") or []):
                return v.get("name")
    return resumes.get("default")


def rule_text(rule: str) -> str:
    if rule.startswith("orderings_disagree"):
        return "answer changed with option order (" + rule.split(":", 1)[-1].replace(",", ", ") + ")"
    return RULE_TEXT.get(rule, rule)


def build(paths: Paths, store: Store, show_all: bool = False, today: datetime | None = None) -> Digest:
    today = today or datetime.now().astimezone()
    clf = build_classifier(paths, offline=True)
    decide = make_decider(load_decision_config(paths))
    phash = profile_hash(load_profile(paths))
    resumes = load_resumes(paths)

    previous = store.runs("report")
    since = None if show_all or not previous else previous[-1]["started_at"]
    latest = store.latest_seen()

    rows = []
    for r in store.results(clf.name):
        decision = json.loads(r["decision"])
        if decision.get("profile_hash") != phash:
            continue
        meta = store.db.execute(
            "SELECT first_seen, last_seen, prefilter FROM postings WHERE id = ?", (r["posting_id"],)
        ).fetchone()
        if not meta or meta["prefilter"] != "kept" or meta["last_seen"] != latest:
            continue
        if since and meta["first_seen"] <= since:
            continue
        p = store.get(r["posting_id"])
        sub = decision["sub_scores"]
        label, rule, sort_score, _ = decide(sub)
        rows.append(Row(p, label, rule, sort_score, sub, suggest_resume(p, resumes), meta["first_seen"]))

    ingests = store.runs("ingest")
    counts = json.loads(ingests[-1]["counts"]) if ingests else {}
    cost = sum(float(x["cost_usd"]) for x in store.runs("classify", since))
    return Digest(today.strftime("%Y-%m-%d"), rows, counts, cost, since, show_all)


# ------------------------------------------------------------------ formatting helpers

def _level(value: float | None, names: tuple[str, ...]) -> str:
    if value is None:
        return "?"
    return names[min(len(names) - 1, max(0, round(float(value) * (len(names) - 1))))]


def scores_text(sub: dict) -> str:
    parts = [
        f"role {_level(sub.get('role_match'), ROLE_LEVELS)}",
        f"interest {_level(sub.get('interest'), INTEREST_LEVELS)}",
        f"level {sub.get('level_fit', '?')}",
        f"apply {round(100 * float(sub.get('verdict__p_apply', 0)))}%",
    ]
    if float(sub.get("hard_blocker", 0)) >= 0.4:
        parts.append(f"blocker {round(100 * float(sub['hard_blocker']))}%")
    return " · ".join(parts)


def salary_text(p: Posting) -> str:
    lo, hi = p.salary_min, p.salary_max
    if lo is None and hi is None:
        return ""
    fmt = lambda x: f"${x / 1000:,.0f}k"  # noqa: E731
    if lo is not None and hi is not None and lo != hi:
        return f"{fmt(lo)}–{fmt(hi)}"
    return fmt(lo if lo is not None else hi)


def where_text(p: Posting) -> str:
    loc = p.locations[0] if p.locations else ""
    more = f" +{len(p.locations) - 1}" if len(p.locations) > 1 else ""
    kind = p.workplace if p.workplace in ("remote", "hybrid") else ""
    return ", ".join(x for x in (loc + more, kind) if x)


def posted_text(p: Posting) -> str:
    return p.posted_at.strftime("%b %d") if p.posted_at else ""


def _md(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def credits(d: Digest) -> list[tuple[str, str]]:
    """Source credits required by the sources' terms, for sources that appear in this report."""
    from jobmatcher.sources import himalayas, hn

    table = {"adzuna": ("Jobs by Adzuna", "https://www.adzuna.com"),
             "himalayas": himalayas.ATTRIBUTION, "hn": hn.ATTRIBUTION}
    shown = {r.posting.source for r in d.rows}
    return [table[s] for s in ("adzuna", "himalayas", "hn") if s in shown]


# ------------------------------------------------------------------ markdown

def to_markdown(d: Digest) -> str:
    c = d.counts
    scope = "all open postings" if d.show_all or not d.since else "new since the last report"
    lines = [
        f"# Job digest {d.date}",
        "",
        f"{len(d.by_label('apply'))} apply · {len(d.by_label('consider'))} consider · "
        f"{len(d.by_label('skip'))} skip ({scope})",
        "",
        f"Latest fetch: {c.get('fetched', 0)} postings, {c.get('new', 0)} new, "
        f"{c.get('kept', 0)} passed the filters. Classifier cost since the last report: ${d.cost_usd:.4f}.",
        "",
    ]
    for label, title in (("apply", "Apply"), ("consider", "Consider")):
        rows = d.by_label(label)
        lines += [f"## {title} ({len(rows)})", ""]
        if not rows:
            lines += ["None.", ""]
            continue
        lines += ["| Job | Company | Where | Pay | Posted | Scores | Why | Resume |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in rows:
            p = r.posting
            lines.append(
                f"| [{_md(p.title)}]({p.url}) | {_md(p.company)} | {_md(where_text(p))} | {salary_text(p)} | "
                f"{posted_text(p)} | {scores_text(r.sub)} | {_md(rule_text(r.rule))} | {_md(r.resume or '')} |"
            )
        lines.append("")
    skips = d.by_label("skip")
    lines += [f"## Skip ({len(skips)})", "", "<details><summary>Show skipped postings</summary>", ""]
    for r in skips:
        lines.append(f"- [{_md(r.posting.title)}]({r.posting.url}), {_md(r.posting.company)}: {rule_text(r.rule)}")
    lines += ["", "</details>", ""]
    for text, url in credits(d):
        lines += [f"[{text}]({url})", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ html

_CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a64;--line:#e4e1d8;--card:#fff;--apply:#1f7a4d;
--consider:#a86a00;--skip:#8a8a84;--link:#1b5fa8}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--muted:#a3a29b;--line:#2f2e2a;
--card:#1f1e1b;--apply:#5cc48f;--consider:#e0a83a;--skip:#8f8e88;--link:#7db3ef}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 10px;display:flex;gap:8px;align-items:center}
.sum{color:var(--muted);margin:0 0 4px}.pill{font-size:12px;font-weight:600;padding:2px 8px;border-radius:99px;
color:#fff}.apply{background:var(--apply)}.consider{background:var(--consider)}.skip{background:var(--skip)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0}
.card a{color:var(--link);font-weight:600;text-decoration:none}.card a:hover{text-decoration:underline}
.meta{color:var(--muted);font-size:13px;margin-top:2px}.why{font-size:13px;margin-top:6px}
.scores{font-size:13px;margin-top:2px;font-variant-numeric:tabular-nums}
details{margin-top:8px}summary{cursor:pointer;color:var(--muted)}
details li{margin:4px 0}details a,.foot a{color:var(--link)}.foot{color:var(--muted);font-size:13px;margin-top:32px}
"""


def _card(r: Row) -> str:
    p = r.posting
    e = html.escape
    meta = " · ".join(x for x in (p.company, where_text(p), salary_text(p),
                                  f"posted {posted_text(p)}" if p.posted_at else "") if x)
    resume = f" · resume: <b>{e(r.resume)}</b>" if r.resume else ""
    return (
        f'<div class="card"><a href="{e(p.url)}" target="_blank" rel="noopener">{e(p.title)}</a>'
        f'<div class="meta">{e(meta)}</div>'
        f'<div class="scores">{e(scores_text(r.sub))}</div>'
        f'<div class="why">Why: {e(rule_text(r.rule))}{resume}</div></div>'
    )


def to_html(d: Digest) -> str:
    e = html.escape
    c = d.counts
    scope = "all open postings" if d.show_all or not d.since else "new since the last report"
    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Job digest {e(d.date)}</title><style>{_CSS}</style></head><body><main>",
        f"<h1>Job digest {e(d.date)}</h1>",
        f"<p class=sum>{len(d.by_label('apply'))} apply · {len(d.by_label('consider'))} consider · "
        f"{len(d.by_label('skip'))} skip ({scope})</p>",
        f"<p class=sum>Latest fetch: {c.get('fetched', 0)} postings, {c.get('new', 0)} new, "
        f"{c.get('kept', 0)} passed the filters. Classifier cost since the last report: ${d.cost_usd:.4f}.</p>",
    ]
    for label, title in (("apply", "Apply"), ("consider", "Consider")):
        rows = d.by_label(label)
        parts.append(f'<h2><span class="pill {label}">{title}</span> {len(rows)}</h2>')
        parts += [_card(r) for r in rows] or ["<p class=sum>None.</p>"]
    skips = d.by_label("skip")
    parts.append(f'<h2><span class="pill skip">Skip</span> {len(skips)}</h2><details>'
                 "<summary>Show skipped postings</summary><ul>")
    for r in skips:
        p = r.posting
        parts.append(f'<li><a href="{e(p.url)}" target="_blank" rel="noopener">{e(p.title)}</a>, '
                     f"{e(p.company)}: {e(rule_text(r.rule))}</li>")
    parts.append("</ul></details>")
    for text, url in credits(d):
        parts.append(f'<p class=foot><a href="{e(url)}">{e(text)}</a></p>')
    parts.append("</main></body></html>")
    return "\n".join(parts)


# ------------------------------------------------------------------ writing

def write(paths: Paths, store: Store, show_all: bool = False, today: datetime | None = None) -> tuple[Digest, Path]:
    d = build(paths, store, show_all=show_all, today=today)
    paths.reports.mkdir(parents=True, exist_ok=True)
    md_path = paths.reports / f"{d.date}.md"
    page = to_html(d)
    md_path.write_text(to_markdown(d), encoding="utf-8")
    (paths.reports / f"{d.date}.html").write_text(page, encoding="utf-8")
    (paths.reports / "latest.html").write_text(page, encoding="utf-8")
    store.record_run("report", {k: len(d.by_label(k)) for k in ("apply", "consider", "skip")})
    return d, md_path
