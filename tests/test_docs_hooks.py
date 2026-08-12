"""Tests for docs/hooks.py's release-fact parsing.

``docs/hooks.py`` is a standalone mkdocs build hook, not part of the
``abicheck`` package -- loaded here the same way ``tests/test_action_reference.py``
loads ``scripts/gen_action_reference.py``, via ``importlib.util.spec_from_file_location``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_hooks() -> ModuleType:
    path = REPO_ROOT / "docs" / "hooks.py"
    spec = importlib.util.spec_from_file_location("docs_hooks", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_reads_latest_release_from_real_repo_facts_json():
    hooks = _load_hooks()
    version = hooks._latest_published_version(REPO_ROOT)
    assert version and version != "unknown"


def test_missing_repo_facts_json_raises(tmp_path):
    hooks = _load_hooks()
    with pytest.raises(hooks.ReleaseFactError):
        hooks._latest_published_version(tmp_path)


def test_malformed_json_raises(tmp_path):
    hooks = _load_hooks()
    (tmp_path / "repo_facts.json").write_text("not json{", encoding="utf-8")
    with pytest.raises(hooks.ReleaseFactError):
        hooks._latest_published_version(tmp_path)


def test_json_list_raises_instead_of_attributeerror(tmp_path):
    """A JSON array has no .get() -- must be a clean ReleaseFactError, not a
    raw AttributeError leaking out of the hook (Codex review, fresh evidence).
    """
    hooks = _load_hooks()
    (tmp_path / "repo_facts.json").write_text(json.dumps(["0.5.0"]), encoding="utf-8")
    with pytest.raises(hooks.ReleaseFactError):
        hooks._latest_published_version(tmp_path)


def test_json_scalar_raises(tmp_path):
    hooks = _load_hooks()
    (tmp_path / "repo_facts.json").write_text(json.dumps("0.5.0"), encoding="utf-8")
    with pytest.raises(hooks.ReleaseFactError):
        hooks._latest_published_version(tmp_path)


def test_non_string_latest_release_raises(tmp_path):
    hooks = _load_hooks()
    (tmp_path / "repo_facts.json").write_text(
        json.dumps({"latest_release": 5}), encoding="utf-8"
    )
    with pytest.raises(hooks.ReleaseFactError):
        hooks._latest_published_version(tmp_path)


def test_empty_string_latest_release_raises(tmp_path):
    hooks = _load_hooks()
    (tmp_path / "repo_facts.json").write_text(
        json.dumps({"latest_release": "   "}), encoding="utf-8"
    )
    with pytest.raises(hooks.ReleaseFactError):
        hooks._latest_published_version(tmp_path)


def test_missing_latest_release_key_raises(tmp_path):
    hooks = _load_hooks()
    (tmp_path / "repo_facts.json").write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(hooks.ReleaseFactError):
        hooks._latest_published_version(tmp_path)


def test_valid_latest_release_is_stripped(tmp_path):
    hooks = _load_hooks()
    (tmp_path / "repo_facts.json").write_text(
        json.dumps({"latest_release": "  0.6.0  "}), encoding="utf-8"
    )
    assert hooks._latest_published_version(tmp_path) == "0.6.0"
