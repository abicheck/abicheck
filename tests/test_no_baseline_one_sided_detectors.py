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

"""``compare --no-baseline`` reports single-snapshot hygiene findings.

Bug class: a detector that audits one snapshot (it never reads ``new``) is
candidate-side hygiene, yet ``compare(None, new)`` ran no detectors at all,
so its findings silently vanished from every no-baseline audit --
``visibility_leak`` on a stripped library exporting ``internal_helper``
reported ``changes: []`` while ``compare libv.so libv.so`` reported the
leak. Invariant: for every candidate, the no-baseline audit reports, marked
candidate-side, exactly the findings each ``one_sided`` detector produces
for that candidate -- and the two-sided report is unchanged (unmarked).

The oracle is a hand-labelled list of which exported names are
internal-looking, not the detector's own name matcher.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker import compare
from abicheck.cli import main as abicheck_main
from abicheck.detector_registry import registry
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

# (exported names, the subset a reviewer labels "internal-looking")
_CASES: list[tuple[list[str], set[str]]] = [
    (
        ["public_api", "internal_helper", "detail_impl"],
        {"internal_helper", "detail_impl"},
    ),
    (["foo", "bar", "compute", "get_version"], set()),
    (["api_open", "_private_state"], {"_private_state"}),
    (["widget_new", "widget_impl", "widget_free"], {"widget_impl"}),
    (["public_api", "__bss_start", "_edata", "_end"], set()),
    (
        [f"x_internal_{i}" for i in range(7)] + ["x_open"],
        {f"x_internal_{i}" for i in range(7)},
    ),
]


def _elf_only(symbols: list[str], *, elf_only_mode: bool = True) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[
            Function(name=s, mangled=s, return_type="?", visibility=Visibility.ELF_ONLY)
            for s in symbols
        ],
        elf_only_mode=elf_only_mode,
    )


@pytest.mark.parametrize(("symbols", "internal"), _CASES)
def test_no_baseline_audit_reports_visibility_leak(
    symbols: list[str], internal: set[str]
) -> None:
    result = run_no_baseline_compare(_elf_only(symbols), candidate_is_live=False)
    leaks = [f for f in result.findings if f.kind is ChangeKind.VISIBILITY_LEAK]
    if not internal:
        assert leaks == []
        return
    assert len(leaks) == 1, result.findings
    (leak,) = leaks
    assert leak.candidate_side_enrichment is True
    assert leak.old_value == str(len(internal))
    shown = {n for n in internal if n in (leak.description or "")}
    assert len(shown) == min(5, len(internal))


@pytest.mark.parametrize(("symbols", "_internal"), _CASES)
def test_no_baseline_matches_one_sided_detectors_on_the_candidate(
    symbols: list[str], _internal: set[str]
) -> None:
    """Metamorphic: the audit's one-sided findings equal a two-sided run's.

    ``compare(X, X)`` runs every detector, including the one-sided ones
    against X. Restricted to the one-sided detectors' kinds, the no-baseline
    audit of X must report the same findings -- marked there, unmarked in
    the two-sided run, which is what keeps ordinary reports unchanged.
    """
    snap = _elf_only(symbols)
    two_sided = compare(snap, snap)
    audit = compare(None, snap)
    one_sided_names = set(registry.one_sided_detector_names)
    assert one_sided_names, "no detector is declared one_sided"
    kinds = {ChangeKind.VISIBILITY_LEAK}

    def _key(c: object) -> tuple[str, str | None, str | None]:
        return (c.kind.value, c.symbol, c.description)  # type: ignore[attr-defined]

    expected = sorted(_key(c) for c in two_sided.changes if c.kind in kinds)
    got = sorted(_key(c) for c in audit.changes if c.kind in kinds)
    assert got == expected
    assert not any(c.candidate_side_enrichment for c in two_sided.changes)
    assert all(c.candidate_side_enrichment for c in audit.changes if c.kind in kinds)


def test_not_applicable_when_headers_were_provided() -> None:
    snap = _elf_only(["internal_helper"], elf_only_mode=False)
    result = run_no_baseline_compare(snap, candidate_is_live=False)
    assert not [f for f in result.findings if f.kind is ChangeKind.VISIBILITY_LEAK]


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")
def test_live_shared_library_audit_reports_visibility_leak(tmp_path: Path) -> None:
    """Audit a real, freshly built, stripped ELF shared library from the CLI."""
    src = tmp_path / "v.c"
    src.write_text(
        "int internal_helper(void){return 1;}\n"
        "int detail_impl(void){return 2;}\n"
        "int public_api(void){return 3;}\n"
    )
    lib = tmp_path / "libv.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(lib), str(src)],
        check=True,
        capture_output=True,
    )
    if shutil.which("strip"):
        subprocess.run(["strip", str(lib)], check=True, capture_output=True)

    out = tmp_path / "audit.json"
    result = CliRunner().invoke(
        abicheck_main, ["compare", "--no-baseline", str(lib), "-o", f"json={out}"]
    )
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        result.output
    )
    assert result.exit_code == 0, result.output
    report = json.loads(out.read_text())
    leaks = [f for f in report["findings"] if f["kind"] == "visibility_leak"]
    assert len(leaks) == 1, report["findings"]
    assert leaks[0]["candidate_side_enrichment"] is True
    assert "internal_helper" in leaks[0]["description"]
    assert "detail_impl" in leaks[0]["description"]
    assert "public_api" not in leaks[0]["description"]
