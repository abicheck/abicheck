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

"""Regression coverage for the third round of the PR #1138/#1140 Mach-O
mangled-identity bug: ``crosscheck``'s own export-table correlation
(``model.export_index.default_versioned_names``, used by
``_check_exported_not_public``/``_check_public_not_exported``) used to
strip a Mach-O export's leading underscore a SECOND time, corrupting every
real Itanium C++ export (``"_ZN2ns3fooEv"`` -> ``"ZN2ns3fooEv"``) even
though ``Function.mangled`` itself is now correctly, singly-stripped at
the point of origin (``extract.headers.clang.context.
strip_darwin_itanium_decoration``, PR #1140's first two commits).

Split out of ``tests/test_cross_source_checks.py`` (that file is already at its
recorded ``architecture/debt.yaml`` no-growth baseline) rather than
growing it further -- see this repo's root ``AGENTS.md`` "Files that are
large — edit carefully": move responsibility to a properly-owned module
instead of trimming to fit.

Root cause: this double-strip bug predates PR #1138/#1140 entirely and was
latent all along, but self-consistently invisible -- before those PRs'
fixes, ``Function.mangled`` for a Mach-O C++ symbol was ITSELF doubly
stripped too (by a since-fixed bug in the castxml+clang hybrid-merge
path), so both sides of the correlation agreed on the same, wrongly
double-stripped spelling. Fixing ``Function.mangled`` at the point of
origin (to the correct, singly-stripped spelling) exposed this second,
independent double-strip in ``model.export_index.default_versioned_names``,
caught by CI's macOS integration-tests lane running against real compiled
Mach-O output (a Linux ELF build never exercises this code path at all).
"""

from __future__ import annotations

from abicheck.buildsource.cross_source_checks import run_crosschecks
from abicheck.checker_policy import ChangeKind
from abicheck.macho_metadata import MachoExport, MachoMetadata
from abicheck.model import AbiSnapshot, Function, ScopeOrigin


def _snap(**kw) -> AbiSnapshot:
    kw.setdefault("library", "libfoo.so")
    kw.setdefault("version", "1.0")
    kw.setdefault("from_headers", True)
    return AbiSnapshot(**kw)


def _findings_of(result, kind: ChangeKind):
    return [c for c in result.findings if c.kind == kind]


class TestMachoExportCorrelationNotDoubleStripped:
    def test_itanium_export_matches_pure_spelling(self) -> None:
        """The actual reported bug's class: a real Itanium C++ export must
        correlate against its own pure spelling, not a doubly-stripped one.
        A second strip here would corrupt ``"_ZN2ns3fooEv"`` to
        ``"ZN2ns3fooEv"``, never correlating against the correctly-
        normalized declaration and wrongly firing both
        ``exported_not_public`` and ``public_not_exported`` for the same,
        unchanged symbol on a self-comparison."""
        snap = _snap(macho=MachoMetadata(exports=[MachoExport(name="_ZN2ns3fooEv")]))
        snap.functions = [
            Function(
                name="foo",
                mangled="_ZN2ns3fooEv",
                return_type="void",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ]
        res = run_crosschecks(snap)
        assert _findings_of(res, ChangeKind.PUBLIC_NOT_EXPORTED) == []
        assert _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC) == []

    def test_itanium_export_with_no_matching_declaration_still_flagged(self) -> None:
        """Negative control: an export that genuinely has no declared
        counterpart must still be flagged -- the fix must not turn off
        correlation altogether, only stop double-stripping it."""
        snap = _snap(
            macho=MachoMetadata(
                exports=[
                    MachoExport(name="_ZN2ns3fooEv"),
                    MachoExport(name="_ZN2ns4bareEv"),
                ]
            )
        )
        snap.functions = [
            Function(
                name="foo",
                mangled="_ZN2ns3fooEv",
                return_type="void",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ]
        res = run_crosschecks(snap)
        hits = _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC)
        assert [c.symbol for c in hits] == ["_ZN2ns4bareEv"]

    def test_namespaced_and_plain_c_exports_correlate_together(self) -> None:
        """Multiple sibling shapes in ONE snapshot, matching the real
        multi-declaration fixture CI's own end-to-end test compiles:
        a namespaced Itanium export and a plain-C bare-name export must
        both correlate correctly at once, not just individually."""
        snap = _snap(
            macho=MachoMetadata(
                exports=[
                    MachoExport(name="_ZN2ns7ns_funcEi"),
                    MachoExport(name="c_func"),
                ]
            )
        )
        snap.functions = [
            Function(
                name="ns_func",
                mangled="_ZN2ns7ns_funcEi",
                return_type="int",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
            Function(
                name="c_func",
                mangled="c_func",
                return_type="int",
                is_extern_c=True,
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
        ]
        res = run_crosschecks(snap)
        assert _findings_of(res, ChangeKind.PUBLIC_NOT_EXPORTED) == []
        assert _findings_of(res, ChangeKind.EXPORTED_NOT_PUBLIC) == []
