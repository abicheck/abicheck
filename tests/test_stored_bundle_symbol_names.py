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

"""A stored-bundle comparison owes the same symbol-name guarantees as any
other comparison.

Sibling of `test_cli_compare_bundle_facts.py`, which owns that command's
operand handling, rejections and scope reporting. This file owns one
crosscutting claim about its *output*: human output demangles, machine
output carries both names. Plan slice 7o made both properties of the
format rather than of a token a user types, and this path was the one that
had honoured neither (Codex/CodeRabbit review, PR #1284).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestStoredBundleMarkdownDemanglesLikeEveryOtherHumanRenderer:
    """Plan slice 7o made demangling a property of the output *format*, so
    the stored-bundle Markdown dispatcher has to honour it too.

    It did not: retiring `--view demangle` left this the one Markdown path
    rendering raw mangled names with no way to ask for readable ones
    (CodeRabbit review, PR #1284). Stated as the format contract rather than
    as the one reported symbol -- the assertion is that this renderer agrees
    with `demangle_text`, the shared pass every other human renderer runs,
    for whatever that pass makes of the finding.
    """

    def _result(self, symbol: str) -> object:
        from abicheck.bundle_models import BundleDiffResult
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change, DiffResult

        diff = DiffResult(
            old_version="old",
            new_version="new",
            library=symbol,
            changes=[
                Change(kind=ChangeKind.FUNC_REMOVED, symbol=symbol, description=symbol)
            ],
            verdict=Verdict.BREAKING,
        )
        return BundleDiffResult(
            old_root=Path("/old"), new_root=Path("/new"), per_library=[diff]
        )

    def _render(self, symbol: str, tmp_path: Path) -> str:
        from abicheck.frontends.cli.commands.compare_bundle_facts import (
            _render_markdown,
        )

        return _render_markdown(
            self._result(symbol),
            old_facts_path=tmp_path / "old.bundlefacts.json",
            new_dir=tmp_path / "new",
        )

    def test_it_renders_what_the_shared_pass_renders(self, tmp_path: Path) -> None:
        """The oracle is `demangle_text` applied to the symbol *token*, an
        independent derivation of what the shared pass does to it -- not a
        hand-written expected string, and not idempotence of the pass over
        the whole document (`demangle_text` is deliberately not idempotent:
        run twice it would demangle the mangled spelling it just placed in
        brackets, which is why every renderer applies it exactly once)."""
        from abicheck.demangle import demangle_text

        symbol = "_ZN3lib4goneEi"
        rendered = self._render(symbol, tmp_path)
        assert demangle_text(symbol) in rendered

    def test_a_mangled_symbol_gains_its_readable_name(self, tmp_path: Path) -> None:
        from abicheck.demangle import demangle

        symbol = "_ZN3lib4goneEi"
        if demangle(symbol) is None:
            pytest.skip("no demangler available in this environment")
        rendered = self._render(symbol, tmp_path)
        assert "lib::gone(int)" in rendered
        # The exact symbol stays copyable beside it -- 7o's whole reason for
        # making demangling automatic rather than a toggle.
        assert symbol in rendered

    def test_a_plain_name_is_untouched(self, tmp_path: Path) -> None:
        """Vacuity guard in the other direction: the pass must not rewrite a
        library path or an unmangled C symbol."""
        rendered = self._render("plain_c_function", tmp_path)
        assert "plain_c_function" in rendered
        assert "[" not in rendered.split("## Bundle findings")[0].split("Library |")[-1]


class TestStoredBundleJsonCarriesBothSymbolNames:
    """The aggregate stored-bundle JSON is a machine projection too, so it
    owes the same "both names" guarantee every other one gives.

    It emitted only the mangled `symbol` (Codex review, PR #1284). The
    oracle is `reporter.resolve_demangled_symbol` -- the same resolver every
    other machine projection uses -- not a hand-written expected string, so
    this agrees with them by construction rather than by coincidence.
    """

    def _result(self, symbol: str) -> object:
        from abicheck.bundle_models import BundleDiffResult, BundleFinding
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import DiffResult

        diff = DiffResult(
            old_version="old",
            new_version="new",
            library="libfoo.so",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )
        return BundleDiffResult(
            old_root=Path("/old"),
            new_root=Path("/new"),
            per_library=[diff],
            bundle_findings=[
                BundleFinding(
                    kind=ChangeKind.BUNDLE_INTRA_DEP_REMOVED,
                    symbol=symbol,
                    description="gone",
                )
            ],
        )

    def _findings(self, symbol: str, tmp_path: Path) -> list[dict]:
        from abicheck.frontends.cli.commands.compare_bundle_facts import _render_json

        payload = json.loads(
            _render_json(
                self._result(symbol),
                old_facts_path=tmp_path / "old.bundlefacts.json",
                new_dir=tmp_path / "new",
            )
        )
        return payload["bundle_findings"]

    def test_a_mangled_finding_carries_both(self, tmp_path: Path) -> None:
        from abicheck.reporter import resolve_demangled_symbol

        symbol = "_ZN3lib4goneEi"
        expected = resolve_demangled_symbol(
            type("_C", (), {"symbol": symbol, "kind": None})()
        )
        if expected is None:
            pytest.skip("no demangler available in this environment")
        finding = self._findings(symbol, tmp_path)[0]
        assert finding["symbol"] == symbol
        assert finding["demangled_symbol"] == expected

    def test_an_unmangled_finding_omits_the_key(self, tmp_path: Path) -> None:
        """Omitted rather than null, matching `_change_to_dict`."""
        finding = self._findings("plain_c_function", tmp_path)[0]
        assert finding["symbol"] == "plain_c_function"
        assert "demangled_symbol" not in finding


class TestTheHumanLedgersDemangleToo:
    """The unconditional stderr ledgers are human output, so they demangle
    like every other human renderer.

    Plan slice 7o made both ledgers unconditional and made demangling a
    property of the format — but both still sent `c.symbol` through raw, so
    the main report read `Readable [mangled]` while the ledger beside it
    stayed mangled, with the override token gone (Codex review, PR #1284).
    Asserted against `demangle_text` itself, the shared pass, rather than a
    hand-written expected string.
    """

    def _change(self, symbol: str):
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change

        return Change(kind=ChangeKind.FUNC_REMOVED, symbol=symbol, description="d")

    def _line(self, symbol: str) -> str:
        from abicheck.cli_audit import _ledger_line

        return _ledger_line(self._change(symbol), False)

    def test_a_mangled_symbol_is_readable_in_the_ledger(self):
        from abicheck.demangle import demangle_text

        symbol = "_ZN3lib4goneEi"
        assert demangle_text(symbol) in self._line(symbol)

    def test_the_exact_symbol_is_still_there(self):
        from abicheck.demangle import demangle

        symbol = "_ZN3lib4goneEi"
        if demangle(symbol) is None:
            pytest.skip("no demangler available in this environment")
        line = self._line(symbol)
        assert "lib::gone(int)" in line
        assert symbol in line

    def test_both_ledgers_share_one_builder(self):
        """They had the same body twice, which is why both were missed.
        Asserted structurally so a future edit to one cannot silently
        diverge from the other."""
        import inspect

        from abicheck import cli_audit

        for fn in (cli_audit.echo_filtered_surface, cli_audit.echo_reconciled):
            assert "_ledger_line(" in inspect.getsource(fn), fn.__name__

    def test_an_unmangled_symbol_is_untouched(self):
        assert "plain_c_function" in self._line("plain_c_function")
