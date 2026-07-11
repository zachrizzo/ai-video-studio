"""Tests for studio config — durable storage paths and agent model (src/studio/config.py)."""

from pathlib import Path

import pytest

from src.studio import config


@pytest.fixture()
def studio_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "studio-home"
    monkeypatch.setenv("STUDIO_HOME", str(home))
    monkeypatch.delenv("STUDIO_GENERATIONS_DIR", raising=False)
    monkeypatch.delenv("STUDIO_PRESETS_FILE", raising=False)
    return home


@pytest.fixture()
def no_legacy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "_LEGACY_DIR", tmp_path / "no-such-legacy")


# ---------------------------------------------------------------------------
# studio_home
# ---------------------------------------------------------------------------


def test_studio_home_env_override_wins(studio_home: Path) -> None:
    assert config.studio_home() == studio_home
    assert studio_home.is_dir()


def test_studio_home_defaults_to_home_dot_video_studio(monkeypatch) -> None:
    monkeypatch.delenv("STUDIO_HOME", raising=False)
    assert config.studio_home() == Path("~/.video-studio").expanduser()


# ---------------------------------------------------------------------------
# generations_dir / presets_file defaults
# ---------------------------------------------------------------------------


def test_generations_dir_defaults_under_studio_home(studio_home: Path, no_legacy) -> None:
    gens = config.generations_dir()
    assert gens == studio_home / "generations"
    assert gens.is_dir()


def test_generations_dir_env_override(studio_home: Path, no_legacy,
                                      tmp_path: Path, monkeypatch) -> None:
    override = tmp_path / "custom-gens"
    monkeypatch.setenv("STUDIO_GENERATIONS_DIR", str(override))
    assert config.generations_dir() == override


def test_presets_file_defaults_under_studio_home(studio_home: Path, no_legacy) -> None:
    assert config.presets_file() == studio_home / "presets.json"


def test_presets_file_env_override(studio_home: Path, no_legacy,
                                   tmp_path: Path, monkeypatch) -> None:
    override = tmp_path / "custom-presets.json"
    monkeypatch.setenv("STUDIO_PRESETS_FILE", str(override))
    assert config.presets_file() == override


# ---------------------------------------------------------------------------
# legacy migration
# ---------------------------------------------------------------------------


@pytest.fixture()
def legacy_dir(tmp_path: Path, monkeypatch) -> Path:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setattr(config, "_LEGACY_DIR", legacy)
    return legacy


def test_presets_migration_copies_legacy_file(studio_home: Path, legacy_dir: Path) -> None:
    (legacy_dir / "presets.json").write_text('{"legacy": true}')
    target = config.presets_file()
    assert target.read_text() == '{"legacy": true}'
    # copy, not move — legacy stays intact for rollback
    assert (legacy_dir / "presets.json").exists()


def test_presets_migration_does_not_overwrite_existing(studio_home: Path,
                                                       legacy_dir: Path) -> None:
    (legacy_dir / "presets.json").write_text('{"legacy": true}')
    studio_home.mkdir(parents=True, exist_ok=True)
    (studio_home / "presets.json").write_text('{"current": true}')
    assert config.presets_file().read_text() == '{"current": true}'


def test_generations_migration_copies_contents(studio_home: Path, legacy_dir: Path) -> None:
    (legacy_dir / "abc123").mkdir()
    (legacy_dir / "abc123" / "status.json").write_text("{}")
    (legacy_dir / "uploads").mkdir()
    (legacy_dir / "uploads" / "img.png").write_text("fake")
    gens = config.generations_dir()
    assert (gens / "abc123" / "status.json").exists()
    assert (gens / "uploads" / "img.png").exists()
    # copy, not move
    assert (legacy_dir / "abc123" / "status.json").exists()


def test_generations_migration_skipped_when_target_nonempty(studio_home: Path,
                                                            legacy_dir: Path) -> None:
    (legacy_dir / "old-gen").mkdir()
    gens = studio_home / "generations"
    gens.mkdir(parents=True)
    (gens / "existing").mkdir()
    result = config.generations_dir()
    assert result == gens
    assert not (gens / "old-gen").exists()


# ---------------------------------------------------------------------------
# agent_model
# ---------------------------------------------------------------------------


def test_agent_model_unset_returns_pinned_default(monkeypatch) -> None:
    """Unset must pin to Sonnet 5, not drift with the local CLI's own default."""
    monkeypatch.delenv("STUDIO_AGENT_MODEL", raising=False)
    assert config.agent_model() == config.DEFAULT_AGENT_MODEL == "claude-sonnet-5"


def test_agent_model_set_returns_override(monkeypatch) -> None:
    monkeypatch.setenv("STUDIO_AGENT_MODEL", "claude-opus-4-8")
    assert config.agent_model() == "claude-opus-4-8"


def test_agent_model_whitespace_returns_pinned_default(monkeypatch) -> None:
    monkeypatch.setenv("STUDIO_AGENT_MODEL", "   ")
    assert config.agent_model() == config.DEFAULT_AGENT_MODEL
