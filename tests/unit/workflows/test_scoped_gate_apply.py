# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""``apply_scoped_gating``: which ADR-043 pass runs, and its typed refusals.

Driven without Click: the workflow layer must report refusals as typed
:class:`ScopedGateError` subclasses carrying facts, and the CLI adapter owns
the wording and exit codes (pinned in ``tests/unit/frontends``).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from abicheck.model.consumer_requirements import LibraryExportsUnreadableError
from abicheck.model.consumer_spec import ConsumerUnreadableError
from abicheck.workflows import consumer_scope_gate as gate
from abicheck.workflows.consumer_scope_gate import (
    ScopedGateError,
    UsedByConsumerError,
    UsedByEvidenceError,
    UsedByLibraryError,
    apply_scoped_gating,
    require_used_by_binary_evidence,
)

_APP = SimpleNamespace(path="app")  # opaque: scope_diff_to_app is always stubbed


def _binary_snapshot() -> SimpleNamespace:
    return SimpleNamespace(elf=object(), pe=None, macho=None)


def _headers_only_snapshot() -> SimpleNamespace:
    return SimpleNamespace(elf=None, pe=None, macho=None)


def _gate(
    *,
    old: Any = None,
    new: Any = None,
    used_by_apps: tuple[Any, ...] = (),
    required_symbols: tuple[str, ...] = (),
    old_input: Path = Path("/nonexistent/old"),
    new_input: Path = Path("/nonexistent/new"),
) -> int | None:
    """Run the gate on a bare result; nothing here reaches Click."""
    return apply_scoped_gating(
        SimpleNamespace(changes=[]),
        old if old is not None else _binary_snapshot(),
        new if new is not None else _binary_snapshot(),
        "strict",
        None,
        used_by_apps=used_by_apps,
        required_symbols=required_symbols,
        used_by_old_input=old_input,
        used_by_new_input=new_input,
        severity=None,
        suppression=None,
    )


# -- require_used_by_binary_evidence ----------------------------------------


def test_real_library_paths_always_qualify(tmp_path: Path) -> None:
    lib = tmp_path / "libfoo.so"
    assert require_used_by_binary_evidence(lib, lib, lib, lib) is None


def test_snapshot_with_binary_block_qualifies() -> None:
    for field in ("elf", "pe", "macho"):
        snap = SimpleNamespace(elf=None, pe=None, macho=None)
        setattr(snap, field, object())
        assert require_used_by_binary_evidence(snap, snap, Path("a"), Path("b")) is None


def test_old_side_without_evidence_is_refused_first() -> None:
    with pytest.raises(UsedByEvidenceError) as info:
        require_used_by_binary_evidence(
            _headers_only_snapshot(),
            _headers_only_snapshot(),
            Path("old.json"),
            Path("new.json"),
        )
    assert info.value.label == "OLD"
    assert info.value.path == Path("old.json")


def test_new_side_without_evidence_is_refused() -> None:
    with pytest.raises(UsedByEvidenceError) as info:
        require_used_by_binary_evidence(
            _binary_snapshot(),
            _headers_only_snapshot(),
            Path("old.json"),
            Path("new.json"),
        )
    assert info.value.label == "NEW"
    assert info.value.path == Path("new.json")


def test_evidence_error_is_a_scoped_gate_error() -> None:
    # The CLI adapter catches the typed refusals by class; they must all share
    # the one base so an unexpected sibling cannot slip past as a bare error.
    assert issubclass(UsedByEvidenceError, ScopedGateError)
    assert issubclass(UsedByConsumerError, ScopedGateError)
    assert issubclass(UsedByLibraryError, ScopedGateError)


# -- apply_scoped_gating: dispatch -------------------------------------------


def test_no_scope_requested_returns_none() -> None:
    assert _gate() is None


def test_used_by_pass_return_value_is_passed_through(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_used_by(result, apps, *args, **kwargs) -> int:
        calls.append({"apps": apps, "severity": kwargs["severity"]})
        return 4

    monkeypatch.setattr(gate, "_apply_used_by_scoping", fake_used_by)
    assert _gate(used_by_apps=(_APP,)) == 4
    assert calls == [{"apps": (_APP,), "severity": None}]


def test_required_symbol_pass_return_value_is_passed_through(monkeypatch) -> None:
    def fake_required(result, symbols, *args, **kwargs) -> int:
        assert symbols == ("init",)
        return 2

    monkeypatch.setattr(gate, "_apply_required_symbol_scoping", fake_required)
    assert _gate(required_symbols=("init",)) == 2


def test_used_by_wins_over_required_symbols(monkeypatch) -> None:
    def must_not_run(*args, **kwargs) -> int:
        raise AssertionError("required-symbol pass ran alongside --used-by")

    monkeypatch.setattr(gate, "_apply_used_by_scoping", lambda *a, **k: 1)
    monkeypatch.setattr(gate, "_apply_required_symbol_scoping", must_not_run)
    assert _gate(used_by_apps=(_APP,), required_symbols=("init",)) == 1


# -- apply_scoped_gating: --used-by error paths ------------------------------


def test_used_by_refuses_side_without_binary_evidence(monkeypatch) -> None:
    def must_not_run(*args, **kwargs) -> int:
        raise AssertionError("scoping ran on evidence-less input")

    monkeypatch.setattr(gate, "_apply_used_by_scoping", must_not_run)
    with pytest.raises(UsedByEvidenceError) as info:
        _gate(
            old=_headers_only_snapshot(),
            used_by_apps=(_APP,),
            old_input=Path("/nonexistent/old.json"),
        )
    assert info.value.label == "OLD"
    assert info.value.path == Path("/nonexistent/old.json")


def test_evidence_refusal_wins_over_required_symbols(monkeypatch) -> None:
    # Pre-existing ordering: --used-by's refusal raises, it does not fall
    # through to the --required-symbol pass.
    monkeypatch.setattr(
        gate,
        "_apply_required_symbol_scoping",
        lambda *a, **k: pytest.fail("fell through to --required-symbol"),
    )
    with pytest.raises(UsedByEvidenceError):
        _gate(
            new=_headers_only_snapshot(),
            used_by_apps=(_APP,),
            required_symbols=("init",),
            new_input=Path("/nonexistent/new.json"),
        )


def test_required_consumer_unreadable_is_typed_and_chained(monkeypatch) -> None:
    error = ConsumerUnreadableError("app: not an ELF")

    def unreadable(*args, **kwargs):
        raise error

    monkeypatch.setattr(
        "abicheck.workflows.consumer_scope.scope_diff_to_app", unreadable
    )
    with pytest.raises(UsedByConsumerError) as info:
        _gate(used_by_apps=(_APP,))
    assert info.value.cause is error
    assert str(info.value) == "app: not an ELF"
    assert info.value.__cause__ is error


def test_library_exports_unreadable_is_typed_and_chained(monkeypatch) -> None:
    error = LibraryExportsUnreadableError("libfoo.so.1: export table was not read")

    def unreadable(*args, **kwargs):
        raise error

    monkeypatch.setattr(
        "abicheck.workflows.consumer_scope.scope_diff_to_app", unreadable
    )
    with pytest.raises(UsedByLibraryError) as info:
        _gate(used_by_apps=(_APP,))
    assert info.value.cause is error
    assert str(info.value) == "libfoo.so.1: export table was not read"
    assert info.value.__cause__ is error


def test_required_symbol_read_error_is_not_wrapped(monkeypatch) -> None:
    # Pinned current behaviour: only --used-by maps consumer-read failures to
    # typed errors. A --required-symbol read error propagates unchanged.
    error = ConsumerUnreadableError("bad")

    def raises(*args, **kwargs):
        raise error

    monkeypatch.setattr(gate, "_apply_required_symbol_scoping", raises)
    with pytest.raises(ConsumerUnreadableError) as info:
        _gate(required_symbols=("init",))
    assert info.value is error
