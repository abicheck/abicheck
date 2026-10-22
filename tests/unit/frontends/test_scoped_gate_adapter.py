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

"""The CLI adapter renders each typed scoped-gate refusal as its click error.

Pins the exact message text and click class (hence exit code: ``UsageError``
-> 64, ``ClickException`` -> 1) the ``--used-by`` flags have always produced.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import click
import pytest

from abicheck.frontends.cli.compare_report import _apply_scoped_gating
from abicheck.model.consumer_requirements import LibraryExportsUnreadableError
from abicheck.model.consumer_spec import ConsumerUnreadableError
from abicheck.workflows import consumer_scope_gate as gate


def _call(old, new, *, old_input: Path, new_input: Path, used_by=(), symbols=()):
    return _apply_scoped_gating(
        SimpleNamespace(changes=[]),
        old,
        new,
        "strict",
        None,
        used_by_apps=used_by,
        required_symbols=symbols,
        used_by_old_input=old_input,
        used_by_new_input=new_input,
        severity=None,
        suppression=None,
    )


def test_evidence_refusal_is_usage_error_with_exact_text() -> None:
    with pytest.raises(click.UsageError) as info:
        _call(
            SimpleNamespace(elf=None, pe=None, macho=None),
            SimpleNamespace(elf=object(), pe=None, macho=None),
            old_input=Path("old.json"),
            new_input=Path("new.json"),
            used_by=(object(),),
        )
    assert info.value.exit_code == 2  # click default; the CLI maps UsageError to 64
    assert info.value.format_message() == (
        "--used-by requires OLD/NEW to be real library binaries, or "
        "JSON snapshots carrying binary evidence (a `dump` of a real "
        "library, not headers-only); OLD (old.json) is neither."
    )


def test_consumer_error_is_click_exception_with_prefix(monkeypatch) -> None:
    def unreadable(*args, **kwargs):
        raise ConsumerUnreadableError("app: not an ELF")

    monkeypatch.setattr(
        "abicheck.workflows.consumer_scope.scope_diff_to_app", unreadable
    )
    with pytest.raises(click.ClickException) as info:
        _call(
            SimpleNamespace(elf=object()),
            SimpleNamespace(elf=object()),
            old_input=Path("/nonexistent/old"),
            new_input=Path("/nonexistent/new"),
            used_by=(object(),),
        )
    assert type(info.value) is click.ClickException
    assert info.value.format_message() == "--used-by consumer: app: not an ELF"


def test_library_error_is_click_exception_with_prefix(monkeypatch) -> None:
    def unreadable(*args, **kwargs):
        raise LibraryExportsUnreadableError("libfoo.so.1: export table was not read")

    monkeypatch.setattr(
        "abicheck.workflows.consumer_scope.scope_diff_to_app", unreadable
    )
    with pytest.raises(click.ClickException) as info:
        _call(
            SimpleNamespace(elf=object()),
            SimpleNamespace(elf=object()),
            old_input=Path("/nonexistent/old"),
            new_input=Path("/nonexistent/new"),
            used_by=(object(),),
        )
    assert type(info.value) is click.ClickException
    assert info.value.format_message() == (
        "--used-by library: libfoo.so.1: export table was not read"
    )


def test_pass_through_return_value(monkeypatch) -> None:
    monkeypatch.setattr(gate, "_apply_required_symbol_scoping", lambda *a, **k: 3)
    assert (
        _call(
            SimpleNamespace(),
            SimpleNamespace(),
            old_input=Path("o"),
            new_input=Path("n"),
            symbols=("init",),
        )
        == 3
    )
