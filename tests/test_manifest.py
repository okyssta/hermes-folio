"""Manifest invariants: what plugin.yaml promises is what the code registers."""

from __future__ import annotations

from pathlib import Path

import yaml

from folio import schemas
from folio.settings import SCHEMA_KEYS

ROOT = Path(__file__).resolve().parent.parent


def _manifest() -> dict:
    return yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))


def test_name_matches_the_plugin_id() -> None:
    # The folder a clone lands in is `hermes-folio`; Hermes keys the plugin off
    # the manifest name, which must stay the catalog id.
    assert _manifest()["name"] == "folio"
    assert _manifest()["label"] == "Folio"


def test_declared_tools_match_the_schemas() -> None:
    manifest = _manifest()
    declared = manifest["provides_tools"]
    assert declared == [schema["name"] for schema in schemas.ALL]
    assert len(set(declared)) == len(declared)


def test_config_schema_matches_the_settings_module() -> None:
    manifest = _manifest()
    assert set(manifest["config_schema"]) == set(SCHEMA_KEYS)


def test_description_discloses_local_behaviour() -> None:
    description = _manifest()["description"]
    assert "Disclosure —" in description
    assert "network" in description.lower()


def test_version_is_a_string() -> None:
    assert isinstance(_manifest()["version"], str)


def test_requires_hermes_is_quoted_semver() -> None:
    assert str(_manifest()["requires_hermes"]).startswith(">=")


def test_desktop_half_opts_in() -> None:
    source = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert "defaultEnabled: false" in source
    for banned in ("querySelector", "MutationObserver", "eval(", "new Function"):
        assert banned not in source
