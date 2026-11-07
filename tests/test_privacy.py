"""Privacy guarantees from the design: each one has a test here, and CI runs them."""

import subprocess
from pathlib import Path

import pytest

from jobmatcher import out, privacy

REPO = Path(__file__).resolve().parents[1]


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("hello\n")
    git(tmp_path, "add", "README.md")
    return tmp_path


def add(repo, rel, text="x\n"):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(repo, "add", "-f", rel)


def test_this_repository_is_clean():
    if not (REPO / ".git").exists():
        pytest.skip("not running inside the git checkout")
    assert privacy.check(REPO) == []


def test_clean_repo_passes(repo):
    add(repo, "user.example/labels.csv")
    add(repo, "tests/fixtures/labels.csv")
    assert privacy.check(repo) == []


@pytest.mark.parametrize(
    "rel",
    ["user/profile.md", "user/data/jobs.sqlite", "data/x.db", "labels.csv", "out/labels.jsonl",
     "reports/2026-11-07.md", ".env", "config/.env.local"],
)
def test_personal_files_are_caught_upstream(repo, rel):
    add(repo, rel)
    assert privacy.check(repo), rel


def test_staged_but_uncommitted_files_count(repo):
    add(repo, "user/filters.yaml")  # staged only: exactly what the pre-commit hook must block
    assert any("user/filters.yaml" in p for p in privacy.check(repo))


def test_api_key_is_caught_but_placeholders_are_not(repo):
    fake_key = "ts_live_" + "abcdefghijklmnop"  # built at runtime so this file passes its own check
    add(repo, "notes.md", "TYPESAFE_API_KEY" + "=" + fake_key + "\n")
    assert any("API key" in p for p in privacy.check(repo))
    (repo / "notes.md").write_text("export TYPESAFE_API_KEY=${{ secrets.TYPESAFE_API_KEY }}\nTYPESAFE_API_KEY=your-key-here\n")
    git(repo, "add", "notes.md")
    assert privacy.check(repo) == []


def test_values_from_local_user_folder_cannot_leak_upstream(repo):
    (repo / "user").mkdir()
    (repo / "user" / "filters.yaml").write_text("location:\n  home_zip: '37402'\n")
    (repo / "user" / "profile.md").write_text(
        "- Built a very specific wellbore-trajectory model for a regional driller in 2024\n"
    )
    add(repo, "src/x.py", "HOME = '37402'\n")
    add(repo, "docs/y.md", "Built a very specific wellbore-trajectory model for a regional driller in 2024\n")
    problems = privacy.check(repo)
    assert any("src/x.py" in p for p in problems) and any("docs/y.md" in p for p in problems)


def test_private_copy_may_track_user_but_not_databases_or_env(repo):
    add(repo, privacy.PRIVATE_MARKER, "")
    add(repo, "user/profile.md")
    add(repo, "user/labels.csv")
    assert privacy.check(repo) == []
    add(repo, "user/data/jobs.sqlite")
    add(repo, ".env")
    assert len(privacy.check(repo)) == 2


upstream_only = pytest.mark.skipif(
    (REPO / privacy.PRIVATE_MARKER).exists(), reason="private copy: user/ is meant to be tracked here"
)


@upstream_only
def test_upstream_has_no_private_marker():
    assert not (REPO / privacy.PRIVATE_MARKER).exists()


@upstream_only
def test_gitignore_ignores_user_and_databases():
    text = (REPO / ".gitignore").read_text()
    lines = {l.strip() for l in text.splitlines()}
    assert {"user/", "*.sqlite", ".env"} <= lines


def test_logs_show_detail_only_off_ci(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    out.detail("Senior Data Scientist at Example Bio: apply")
    out.info("12 postings classified, $0.002")
    printed = capsys.readouterr().out
    assert "Example Bio" not in printed and "12 postings" in printed
    monkeypatch.delenv("GITHUB_ACTIONS")
    monkeypatch.delenv("CI", raising=False)
    out.detail("visible locally")
    assert "visible locally" in capsys.readouterr().out


def test_example_user_is_marked_fictional():
    for name in ("profile.md", "filters.yaml", "companies.yaml", "resumes.yaml"):
        assert "FICTIONAL" in (REPO / "user.example" / name).read_text(), name
