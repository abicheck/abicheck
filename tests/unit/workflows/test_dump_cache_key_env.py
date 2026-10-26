"""The dump cache key folds in the environment include variables.

castxml inherits the process environment, so ``CPATH``, ``C_INCLUDE_PATH`` and
``CPLUS_INCLUDE_PATH`` change the parse without appearing in any argument the
key hashes. Each one must move the key; nothing else may.
"""

from __future__ import annotations

import pytest

from abicheck.extract.compiler_driver import ENV_INCLUDE_VARS
from abicheck.workflows.dump.cache import key as cache_key

_ALL_VARS = ("CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH")


def _key() -> str:
    return cache_key._dump_cache_extra_key(
        "elf", "castxml", None, None, "c++", uses_ast=True
    )


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ALL_VARS:
        monkeypatch.delenv(var, raising=False)


def test_shared_constant_names_the_three_variables() -> None:
    assert ENV_INCLUDE_VARS == _ALL_VARS


@pytest.mark.parametrize("var", _ALL_VARS)
def test_key_changes_when_each_include_variable_changes(
    monkeypatch: pytest.MonkeyPatch, var: str
) -> None:
    _clear_env(monkeypatch)
    unset_key = _key()
    monkeypatch.setenv(var, "/opt/inc-a")
    set_key = _key()
    monkeypatch.setenv(var, "/opt/inc-b")
    other_key = _key()
    assert set_key != unset_key
    assert other_key != set_key
    assert other_key != unset_key


@pytest.mark.parametrize("var", _ALL_VARS)
def test_key_is_stable_when_only_other_state_changes(
    monkeypatch: pytest.MonkeyPatch, var: str
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(var, "/opt/inc")
    first = _key()
    assert _key() == first
    monkeypatch.setenv("ABICHECK_UNRELATED_KEY_PROBE", "1")
    assert _key() == first


def test_empty_and_unset_include_variables_key_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    unset_key = _key()
    for var in _ALL_VARS:
        monkeypatch.setenv(var, "")
    assert _key() == unset_key


def test_binary_only_key_does_not_depend_on_include_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A binary-only dump never runs an AST frontend, so the variables are
    # irrelevant there and must not split its cache entries.
    _clear_env(monkeypatch)
    before = cache_key._dump_cache_extra_key(
        "elf", "castxml", None, None, "c++", uses_ast=False
    )
    monkeypatch.setenv("CPATH", "/opt/inc")
    after = cache_key._dump_cache_extra_key(
        "elf", "castxml", None, None, "c++", uses_ast=False
    )
    assert after == before
