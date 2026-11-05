from pathlib import Path

import pytest

from repetui import config as config_module
from repetui.config import ProfileNotFoundError, default_anki_base, discover_profile


def _profile(base: Path, name: str) -> Path:
    directory = base / name
    directory.mkdir(parents=True)
    collection = directory / "collection.anki2"
    collection.touch()
    return collection


def test_discovers_the_only_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    collection = _profile(tmp_path, "whskr")
    monkeypatch.setenv("REPETUI_ANKI_HOME", str(tmp_path))

    result = discover_profile()

    assert result.name == "whskr"
    assert result.collection == collection


def test_multiple_profiles_require_an_explicit_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _profile(tmp_path, "one")
    _profile(tmp_path, "two")
    monkeypatch.setenv("REPETUI_ANKI_HOME", str(tmp_path))

    with pytest.raises(ProfileNotFoundError, match="--profile"):
        discover_profile()


def test_collection_override_does_not_require_standard_anki_location(tmp_path: Path) -> None:
    collection = _profile(tmp_path / "custom", "study")

    result = discover_profile(collection_override=collection)

    assert result.name == "study"
    assert result.collection == collection.resolve()


def test_macos_default_is_application_support(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("REPETUI_ANKI_HOME", raising=False)
    monkeypatch.setattr(config_module.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert default_anki_base() == tmp_path / "Library" / "Application Support" / "Anki2"


def test_linux_default_is_unchanged(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("REPETUI_ANKI_HOME", raising=False)
    monkeypatch.setattr(config_module.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert default_anki_base() == tmp_path / ".local" / "share" / "Anki2"


def test_override_wins_on_macos(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("REPETUI_ANKI_HOME", str(tmp_path / "x"))
    monkeypatch.setattr(config_module.sys, "platform", "darwin")
    assert default_anki_base() == tmp_path / "x"
