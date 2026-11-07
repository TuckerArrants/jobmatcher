from jobmatcher.cli import main
from jobmatcher.findboard import candidate_tokens, find_board


def test_candidate_tokens():
    toks = candidate_tokens("Example Bio, Inc.")
    assert toks[:3] == ["examplebio", "example-bio", "example"]
    assert candidate_tokens("") == []


def test_find_board(fake_http):
    hits = find_board("Example Bio", fake_http)
    assert [(h.ats, h.token, h.jobs) for h in hits] == [("greenhouse", "examplebio", 3)]


def test_cli_reports_config_errors(tmp_path, capsys):
    (tmp_path / "companies.yaml").write_text("companies:\n  - name: A\n")
    assert main(["--user-dir", str(tmp_path), "ingest"]) == 2
    assert "needs name, ats and token" in capsys.readouterr().err
