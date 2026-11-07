from jobmatcher import dotenv


def test_dotenv_loads_without_overriding(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# my keys\nJM_TEST_A=abc\nexport JM_TEST_B="quoted value"\nJM_TEST_C=from-file\nnot a line\n')
    monkeypatch.delenv("JM_TEST_A", raising=False)
    monkeypatch.delenv("JM_TEST_B", raising=False)
    monkeypatch.setenv("JM_TEST_C", "from-env")
    loaded = dotenv.load(env)
    import os

    assert os.environ["JM_TEST_A"] == "abc"
    assert os.environ["JM_TEST_B"] == "quoted value"
    assert os.environ["JM_TEST_C"] == "from-env"  # real environment wins
    assert loaded == ["JM_TEST_A", "JM_TEST_B"]  # names only, never values


def test_missing_file_is_fine(tmp_path):
    assert dotenv.load(tmp_path / "nope.env") == []
