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

"""Every finding that is evidence of a lost ELF export carries the old-side
symbol binding, so a ``binding:`` selector can reach it.

The bug class: a detector emits an export-loss finding without
``Change.symbol_binding``. The selector then matches nothing for that kind,
and a WEAK loss a consumer policy demotes stays BREAKING. PRs #1629 and #1630
fixed the emitters found so far; this guard holds the whole set
``EXPORT_LOSS_KINDS`` to the contract, so a new emitter that forgets the field
fails here instead of in a consumer's CI.

The contract is stated against the ELF table, not against the emitter: when the
old library's ``.dynsym`` has an unambiguous entry for every symbol a finding
names, the finding's ``symbol_binding`` must be that entry's binding. A name
whose versioned aliases disagree, or that the old table never had, may read
``None``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.elf_metadata import ElfMetadata, parse_elf_metadata
from tests._export_loss_binding import assert_binding_matches_old_elf, is_export_loss

# scripts/ is not a package; the catalog resolver lives there (as in test_abi_examples.py).
REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_DIR / "scripts"))
import example_catalog  # noqa: E402

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="ELF tests require Linux"),
    pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++"),
]

# Shared header. ``Pub`` exposes the internal ``detail::P`` template, so its
# instantiations are reachable from the public surface.
_HEADER = (
    "namespace ns {\n"
    "namespace detail {\n"
    "template<class T> struct P { virtual ~P(); int f() { return 1; } };\n"
    "template<class T> P<T>::~P() {}\n"
    "}\n"
    "struct Pub { detail::P<int> p; int g(); detail::P<float>* q(); };\n"
    "inline int hdr_inline() { return 5; }\n"  # header-declared inline
    "int strong_fn();\n"
    "}\n"
)

# OLD exports four WEAK symbols that NEW loses, one per path:
#   * ``hdr_inline``        header-declared inline function
#   * ``weak_undeclared``   WEAK export no public header declares
#   * ``P<float>`` members  template instantiation in the internal ``detail``
#                           namespace
# NEW is built with ``-fvisibility-inlines-hidden``, which hides the inline and
# template WEAK exports; NEW's source also drops ``weak_undeclared``.
_OLD_SOURCE = (
    '#include "a.h"\n'
    "namespace ns {\n"
    "int Pub::g() { return 2 + hdr_inline(); }\n"
    "detail::P<float>* Pub::q() { return new detail::P<float>(); }\n"
    "int strong_fn() { return 3; }\n"
    "__attribute__((weak)) int weak_undeclared() { return 7; }\n"
    "}\n"
)
_NEW_SOURCE = (
    '#include "a.h"\n'
    "namespace ns {\n"
    "int Pub::g() { return 2 + hdr_inline(); }\n"
    "detail::P<float>* Pub::q() { return new detail::P<float>(); }\n"
    "int strong_fn() { return 3; }\n"
    "}\n"
)


def _build(directory: Path, name: str, source: str, *flags: str) -> Path:
    (directory / f"{name}.cpp").write_text(source, encoding="utf-8")
    so = directory / f"lib{name}.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", *flags, f"{name}.cpp", "-o", str(so)],
        cwd=directory,
        check=True,
    )
    return so


def _run_two_versions(
    tmp_path: Path, *, with_headers: bool
) -> tuple[dict, ElfMetadata]:
    """Dump both versions, read OLD's ELF table, delete OLD's binary, then
    compare the stored snapshots. ``with_headers`` selects the header-depth
    dump (L2) or the binary-only dump (L0). Returns the JSON report and the
    OLD ELF table, read before the binary is gone."""
    (tmp_path / "a.h").write_text(_HEADER, encoding="utf-8")
    old_so = _build(tmp_path, "old", _OLD_SOURCE)
    new_so = _build(tmp_path, "new", _NEW_SOURCE, "-fvisibility-inlines-hidden")
    old_elf = parse_elf_metadata(old_so)
    old_json = tmp_path / "old.json"
    new_json = tmp_path / "new.json"
    header_args = ["-H", str(tmp_path / "a.h")] if with_headers else []
    for so, out in ((old_so, old_json), (new_so, new_json)):
        dumped = CliRunner().invoke(
            main, ["dump", str(so), *header_args, "-o", str(out)]
        )
        assert dumped.exit_code == 0, dumped.output
    old_so.unlink()
    out_json = tmp_path / "out.json"
    result = CliRunner().invoke(
        main,
        ["compare", str(old_json), str(new_json), "-o", f"json={out_json}"],
    )
    assert result.exit_code in (0, 4), result.output
    return json.loads(out_json.read_text(encoding="utf-8")), old_elf


def _elf_names_for(change: dict) -> list[str]:
    """The old-side mangled names a finding is about: its own symbol plus each
    removed instantiation it lists."""
    names = [change["symbol"]]
    names += [r["mangled"] for r in change.get("removed_instantiations") or []]
    return names


def check_export_loss_bindings(changes: list[dict], old_elf: ElfMetadata) -> int:
    """Check the contract over every export-loss finding in *changes*. Returns
    how many there were, so callers can assert the check was not vacuous."""
    checked = 0
    for change in changes:
        if not is_export_loss(change["kind"]):
            continue
        checked += 1
        assert_binding_matches_old_elf(
            change["kind"],
            change["symbol"],
            change.get("symbol_binding"),
            _elf_names_for(change),
            old_elf,
        )
    return checked


# Kinds the fixture must produce at each depth. Headers (L2) distinguish the
# header-declared inline from the undeclared WEAK export, so they yield
# func_visibility_changed. The binary-only run (L0) instead reports the
# internal-namespace template stem as internal_template_leaks_via_public_api.
_KINDS_WITH_HEADERS = {"func_visibility_changed", "func_removed_elf_only"}
_KINDS_BINARY_ONLY = {"func_removed_elf_only", "internal_template_leaks_via_public_api"}


@pytest.mark.parametrize(
    ("with_headers", "expected_kinds"),
    [(True, _KINDS_WITH_HEADERS), (False, _KINDS_BINARY_ONLY)],
    ids=["headers-L2", "binary-L0"],
)
def test_every_export_loss_finding_carries_old_binding(
    tmp_path: Path, with_headers: bool, expected_kinds: set[str]
) -> None:
    report, old_elf = _run_two_versions(tmp_path, with_headers=with_headers)
    changes = report["changes"]
    kinds = {c["kind"] for c in changes}
    # Non-vacuity: the fixture must exercise the paths it claims to cover.
    assert expected_kinds <= kinds, sorted(kinds)
    assert check_export_loss_bindings(changes, old_elf) >= len(expected_kinds)


# Every export OLD loses in this fixture is WEAK, so every export-loss finding
# about one of them must read "weak". The template stem is the aggregate over
# its removed WEAK instantiations.
_WEAK_LOST_SYMBOLS = {
    "_ZN2ns15weak_undeclaredEv",  # undeclared WEAK export
    "_ZN2ns10hdr_inlineEv",  # header-declared inline
    "_ZN2ns6detail1PIfEC1Ev",  # P<float> instantiation, ctor
    "_ZN2ns6detail1PIfEC2Ev",
    "ns::detail::P::P",  # internal-namespace template stem (binary-only run)
}


@pytest.mark.parametrize("with_headers", [True, False], ids=["headers-L2", "binary-L0"])
def test_export_loss_findings_stamp_weak_for_weak_exports(
    tmp_path: Path, with_headers: bool
) -> None:
    report, _ = _run_two_versions(tmp_path, with_headers=with_headers)
    lost = [
        c
        for c in report["changes"]
        if is_export_loss(c["kind"]) and c["symbol"] in _WEAK_LOST_SYMBOLS
    ]
    seen = {c["symbol"] for c in lost}
    assert seen & _WEAK_LOST_SYMBOLS, sorted(seen)
    assert all(c.get("symbol_binding") == "weak" for c in lost), [
        (c["kind"], c["symbol"], c.get("symbol_binding")) for c in lost
    ]


# The catalog cases built from this same pattern, with their policy files.
_CATALOG_CASES = [
    "case217_export_loss_weak_binding_demoted",
    "case218_export_loss_strong_binding_stays_breaking",
]


@pytest.mark.parametrize("case", _CATALOG_CASES)
def test_catalog_case_pair_verdicts_match_ground_truth(
    tmp_path: Path, case: str
) -> None:
    """The committed case pair gives the verdicts its ground-truth entry records:
    ``expected`` without a policy (the default-policy verdict), and
    ``policy_expected`` with the case's own policy.yaml."""
    case_dir = example_catalog.CASES_DIR / case
    entry = json.loads(example_catalog.GROUND_TRUTH_PATH.read_text(encoding="utf-8"))[
        "verdicts"
    ][case]
    for name in ("v1", "v2"):
        shutil.copy(case_dir / f"{name}.h", tmp_path / f"{name}.h")
    old_so = _build(tmp_path, "v1", (case_dir / "v1.cpp").read_text(encoding="utf-8"))
    new_so = _build(tmp_path, "v2", (case_dir / "v2.cpp").read_text(encoding="utf-8"))
    old_json = tmp_path / "v1.json"
    new_json = tmp_path / "v2.json"
    for so, header, out in ((old_so, "v1.h", old_json), (new_so, "v2.h", new_json)):
        dumped = CliRunner().invoke(
            main, ["dump", str(so), "-H", str(tmp_path / header), "-o", str(out)]
        )
        assert dumped.exit_code == 0, dumped.output
    runs = [("expected", [])]
    if "policy_expected" in entry:
        runs.append(("policy_expected", ["--policy", str(case_dir / "policy.yaml")]))
    for key, extra in runs:
        out_json = tmp_path / f"out-{key}.json"
        result = CliRunner().invoke(
            main,
            ["compare", str(old_json), str(new_json), "-o", f"json={out_json}", *extra],
        )
        report = json.loads(out_json.read_text(encoding="utf-8"))
        assert report["verdict"] == entry[key], (key, result.output)
        assert result.exit_code == (0 if entry[key] == "COMPATIBLE_WITH_RISK" else 4)
