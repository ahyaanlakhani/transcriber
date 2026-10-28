from transcriber.config import PROJECT_ROOT, load_config


def test_reads_values_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_API_KEY=gsk_file\nTRANSCRIBER_PORT=9001\n", encoding="utf-8")
    config = load_config(env_file, environ={})
    assert config.groq_api_key == "gsk_file"
    assert config.port == 9001


def test_environment_overrides_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_API_KEY=gsk_file\n", encoding="utf-8")
    config = load_config(env_file, environ={"GROQ_API_KEY": "gsk_env", "TRANSCRIBER_DATA_DIR": str(tmp_path)})
    assert config.groq_api_key == "gsk_env"
    assert config.data_dir == tmp_path


def test_defaults_when_nothing_set(tmp_path):
    config = load_config(tmp_path / "missing.env", environ={})
    assert config.groq_api_key is None
    assert config.data_dir == PROJECT_ROOT / "data"
    assert config.port == 8000


def test_blank_key_counts_as_missing(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_API_KEY=   \n", encoding="utf-8")
    assert load_config(env_file, environ={}).groq_api_key is None


def test_notes_settings_and_keys(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_API_KEY=gsk_x\nCEREBRAS_API_KEY=csk_y\nNOTES_CHECKER=none\n", encoding="utf-8")
    config = load_config(env_file, environ={"GEMINI_API_KEY": "g_z"})
    assert config.api_keys == {"cerebras": "csk_y", "gemini": "g_z", "groq": "gsk_x"}
    assert config.notes_writer == "groq:openai/gpt-oss-120b"
    assert config.notes_checker == "none"


def test_extra_languages_and_the_other_language_come_from_the_env_file(tmp_path):
    from transcriber.config import load_config
    nothing = tmp_path / "empty.env"  # not the real .env, which may name languages of its own
    nothing.write_text("", encoding="utf-8")
    config = load_config(nothing, environ={"EXTRA_LANGUAGES": "es:Spanish, FR:French, nonsense, x:Too short, auto:Clash, abcd:Too long",
                                  "SECOND_LANGUAGE": "Spanish"})
    assert dict(config.extra_languages) == {"es": "Spanish", "fr": "French"}  # only code:Name entries with a real code
    assert config.second_language == "Spanish"
    assert load_config(nothing, environ={}).extra_languages == {} and load_config(nothing, environ={}).second_language == ""


def test_the_app_offers_the_languages_it_was_configured_with(tmp_path):
    from fastapi.testclient import TestClient
    from transcriber.app import create_app
    from transcriber.config import Config, LANGUAGES, LANGUAGE_NAMES
    config = Config(groq_api_key=None, data_dir=tmp_path / "data", port=0, extra_languages={"pt": "Portuguese"},
                    second_language="Portuguese")
    with TestClient(create_app(config, start_worker=False, privacy_allow_all=True)) as client:
        status = client.get("/api/status").json()
        names = {item["code"]: item["name"] for item in status["languages"]}
        assert names["pt"] == "Portuguese" and names["mixed"] == "English + Portuguese (mixed)" and status["second_language"] == "Portuguese"
        assert "pt" in LANGUAGES and LANGUAGE_NAMES["en"] == "English"
        recorded = client.put("/api/settings", json={"default_language": "pt"})
        assert recorded.status_code == 200 and recorded.json()["default_language"] == "pt"
        assert client.put("/api/settings", json={"default_language": "xx"}).status_code == 400  # not in the menu
    # a plain install (nothing in .env) names no language at all
    from transcriber.config import register_languages
    register_languages({}, "")
    assert LANGUAGE_NAMES["mixed"] == "English + your other language (mixed)"
    register_languages({"es": "Spanish"}, "Spanish")  # put the shared test setup back
