from karaoke_he import config


def test_separation_backend_auto_uses_modal_only_when_credentials_exist(monkeypatch, tmp_path):
    monkeypatch.delenv("KARAOKIT_SEPARATION", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path)
    assert config.separation_backend() == "local"

    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-123")
    assert config.separation_backend() == "modal"

    monkeypatch.setenv("KARAOKIT_SEPARATION", "local")
    assert config.separation_backend() == "local"


def test_youtube_is_off_unless_enabled(monkeypatch):
    monkeypatch.delenv("KARAOKIT_ENABLE_YOUTUBE", raising=False)
    assert config.youtube_enabled() is False
    monkeypatch.setenv("KARAOKIT_ENABLE_YOUTUBE", "true")
    assert config.youtube_enabled() is True


def test_dotenv_does_not_override_real_environment(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nKARAOKIT_TEST_A=from_file\nKARAOKIT_TEST_B='quoted'\n")
    monkeypatch.setenv("KARAOKIT_TEST_A", "from_env")
    monkeypatch.delenv("KARAOKIT_TEST_B", raising=False)
    config._load_dotenv(env)
    import os
    assert os.environ["KARAOKIT_TEST_A"] == "from_env"
    assert os.environ["KARAOKIT_TEST_B"] == "quoted"
    monkeypatch.delenv("KARAOKIT_TEST_B")
