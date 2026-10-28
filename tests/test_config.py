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
