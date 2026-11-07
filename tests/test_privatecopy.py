import subprocess
from pathlib import Path

import yaml

from jobmatcher import privacy, privatecopy
from jobmatcher.findboard import add_to_companies, best, BoardHit

REPO = Path(__file__).resolve().parents[1]


def test_convert_unignores_user_but_keeps_databases_ignored(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text((REPO / ".gitignore").read_text())
    assert privatecopy.convert(tmp_path) is True
    text = (tmp_path / ".gitignore").read_text()
    assert "user/" not in {l.strip() for l in text.splitlines()}
    assert "*.sqlite" in text and ".env" in text
    assert privatecopy.is_private(tmp_path)
    assert privatecopy.convert(tmp_path) is False  # idempotent

    (tmp_path / "user" / "data").mkdir(parents=True)
    (tmp_path / "user" / "profile.md").write_text("x")
    (tmp_path / "user" / "data" / "jobs.sqlite").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    tracked = privacy.tracked_files(tmp_path)
    assert "user/profile.md" in tracked
    assert "user/data/jobs.sqlite" not in tracked  # still ignored
    assert privacy.check(tmp_path) == []


def test_add_to_companies_skips_existing(tmp_path):
    path = tmp_path / "companies.yaml"
    path.write_text("companies:\n  - {name: A, ats: lever, token: a}\n")
    n = add_to_companies(path, [{"name": "a", "ats": "ashby", "token": "a"}, {"name": "B", "ats": "lever", "token": "b"}])
    assert n == 1
    assert [c["name"] for c in yaml.safe_load(path.read_text())["companies"]] == ["A", "B"]


def test_best_prefers_full_name_over_bigger_board():
    # "Arcadia Science": the full-name board wins over a bigger board for the first word only.
    hits = [BoardHit("lever", "arcadiascience", 7), BoardHit("lever", "arcadia", 40)]
    assert best(hits).token == "arcadiascience"
    assert best([BoardHit("lever", "x", 2), BoardHit("greenhouse", "x", 9)]).ats == "greenhouse"
    assert best([]) is None


def test_daily_workflow_refuses_public_repos_and_passes_no_secrets_to_logs():
    wf = (REPO / ".github" / "workflows" / "daily.yml").read_text()
    assert "Refusing to run" in wf and ".private" in wf
    assert "privacy.py" in wf  # results are privacy-checked before every commit
    assert "set -x" not in wf
