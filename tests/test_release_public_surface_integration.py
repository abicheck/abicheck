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

"""The measured two-library reproduction, end to end through the CLI.

The minimal reproduction of the release Cartesian-product defect, preserved
as a regression test exactly as it was measured. One shared umbrella header
declares ``api_a`` and ``api_b``; ``libA`` exports ``api_a`` and ``libB``
exports ``api_b``. Before the release product model, a directory comparison
reported ``public_not_exported`` **twice** -- ``api_b`` demanded from
``libA``, ``api_a`` demanded from ``libB`` -- because the complete product
header surface was compared against each DSO independently. Measured on the
real fixture before the change:

    libA.so  NO_CHANGE  risk_changes=1  public_not_exported: api_b
    libB.so  NO_CHANGE  risk_changes=1  public_not_exported: api_a

and after it: no such finding on either member, the contract reconciled once
against the union of the bundle's exports.

The three extensions the reproduction is then put through -- a declaration
removed from the whole bundle, an undocumented export added to one member, a
changed public type -- are each their own test class below. Marked
``integration``: this needs a real compiler *and* a real header-AST frontend
(``castxml``), because the point is the whole CLI path, not a hand-built
snapshot. The cardinality invariants themselves are stated at unit speed in
``tests/test_release_public_surface.py``; this file is the end-to-end
witness that they hold through ``abicheck compare``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = pytest.mark.integration

_HEADER = """\
#pragma once
struct Cfg {{ int a; {extra} }};
#ifdef __cplusplus
extern "C" {{
#endif
int api_a(struct Cfg* c);
int api_b(struct Cfg* c);
#ifdef __cplusplus
}}
#endif
"""

_SRC_A = '#include "product.h"\nint api_a(struct Cfg* c) { return c->a; }\n'
_SRC_B = '#include "product.h"\nint api_b(struct Cfg* c) { return c->a; }\n'


def _require_toolchain() -> None:
    for tool in ("gcc", "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is required for the release-product-model fixture")


def _build_product(
    root: Path,
    *,
    extra_field: str = "",
    src_b: str = _SRC_B,
) -> Path:
    """A two-library product with one shared umbrella header.

    Canonical, non-symlinked paths throughout (`root` is resolved first):
    symlink/path provenance matching is deliberately out of scope for this
    fixture, and on macOS pytest's own `tmp_path` lives under a symlinked
    `/var` -> `/private/var`, so resolving here keeps that question out of
    every assertion below.
    """
    root = root.resolve()
    include = root / "include"
    lib = root / "lib"
    include.mkdir(parents=True)
    lib.mkdir(parents=True)
    (include / "product.h").write_text(
        _HEADER.format(extra=extra_field), encoding="utf-8"
    )
    for name, source in (("a", _SRC_A), ("b", src_b)):
        src = root / f"{name}.c"
        src.write_text(source, encoding="utf-8")
        subprocess.run(
            [
                "gcc",
                "-shared",
                "-fPIC",
                "-o",
                str(lib / f"lib{name.upper()}.so"),
                str(src),
                f"-I{include}",
            ],
            check=True,
            capture_output=True,
        )
    return root


def _compare(tmp_path: Path, old: Path, new: Path) -> dict:
    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old / "lib"),
            str(new / "lib"),
            "--header",
            f"old={old / 'include'}",
            "--header",
            f"new={new / 'include'}",
            "-o",
            f"json={out}",
        ],
    )
    assert out.exists(), result.output
    return json.loads(out.read_text(encoding="utf-8"))


def _kinds(report: dict, library: str) -> list[str]:
    for entry in report["libraries"]:
        if entry["library"] == library:
            return [f["kind"] for f in entry.get("findings", [])]
    raise AssertionError(
        f"{library} missing from {[e['library'] for e in report['libraries']]}"
    )


@pytest.fixture
def unchanged_product(tmp_path: Path) -> tuple[Path, Path]:
    _require_toolchain()
    return (
        _build_product(tmp_path / "old"),
        _build_product(tmp_path / "new"),
    )


class TestOneContractManyProviders:
    """The reproduction itself: neither member is short of the other's
    declaration."""

    def test_no_member_reports_a_siblings_declaration_as_missing(
        self, unchanged_product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        report = _compare(tmp_path, *unchanged_product)
        assert "public_not_exported" not in _kinds(report, "libA.so")
        assert "public_not_exported" not in _kinds(report, "libB.so")

    def test_no_member_reports_a_shared_declaration_as_undocumented(
        self, unchanged_product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        report = _compare(tmp_path, *unchanged_product)
        assert "exported_not_public" not in _kinds(report, "libA.so")
        assert "exported_not_public" not in _kinds(report, "libB.so")

    def test_the_contract_is_reconciled_once_against_the_union(
        self, unchanged_product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        section = _compare(tmp_path, *unchanged_product)[
            "public_surface_reconciliation"
        ]
        side = section["sides"]["new"]
        assert side["public_declarations_with_export_obligation"] == 2
        assert side["satisfied_by_bundle_exports"] == 2
        assert side["missing_from_bundle"] == []
        assert side["coverage_complete"] is True

    def test_the_header_surface_is_acquired_once_per_side(
        self, unchanged_product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """Requirement: an identical directory comparison performs at most
        one header acquisition per side -- not one per member."""
        section = _compare(tmp_path, *unchanged_product)[
            "public_surface_reconciliation"
        ]
        assert section["acquisition"]["acquisitions"] <= 2

    def test_the_release_stays_clean(
        self, unchanged_product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """No contract finding, and nothing gating. Deliberately not a
        verdict whitelist: on macOS the two separately-built dylibs differ
        in their `LC_ID_DYLIB` install name, a real (non-gating) observation
        about the fixture rather than anything this test is about."""
        report = _compare(tmp_path, *unchanged_product)
        assert report["exit"]["code"] == 0
        assert report["public_surface_reconciliation"]["missing_exports"] == []
        for library in ("libA.so", "libB.so"):
            kinds = _kinds(report, library)
            assert "public_not_exported" not in kinds
            assert "exported_not_public" not in kinds


class TestDeclarationRemovedFromTheWholeBundle:
    """Extension 1: ``api_b`` gone from the new bundle entirely."""

    @pytest.fixture
    def product(self, tmp_path: Path) -> tuple[Path, Path]:
        _require_toolchain()
        return (
            _build_product(tmp_path / "old"),
            _build_product(
                tmp_path / "new",
                src_b='#include "product.h"\nint unrelated_b(int x) { return x; }\n',
            ),
        )

    def test_exactly_one_product_level_finding(
        self, product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        section = _compare(tmp_path, *product)["public_surface_reconciliation"]
        missing = section["missing_exports"]
        assert [f["symbol"] for f in missing] == ["api_b"]
        assert missing[0]["kind"] == "public_not_exported"
        assert missing[0]["scope"] == "release"

    def test_it_is_not_duplicated_onto_either_member(
        self, product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        report = _compare(tmp_path, *product)
        assert "public_not_exported" not in _kinds(report, "libA.so")
        assert "public_not_exported" not in _kinds(report, "libB.so")

    def test_it_is_stated_as_introduced_by_this_release(
        self, product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        section = _compare(tmp_path, *product)["public_surface_reconciliation"]
        assert section["missing_exports"][0]["cross_source_evolution"] == "introduced"

    def test_the_declaration_evidence_rides_the_finding(
        self, product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        section = _compare(tmp_path, *product)["public_surface_reconciliation"]
        finding = section["missing_exports"][0]
        assert "product.h" in finding["source_location"]
        assert "api_b" in finding["description"]

    def test_the_real_binary_removal_stays_per_library(
        self, product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """Binary evolution is still the member's own: only ``libB`` lost a
        symbol, and only ``libB``'s entry says so."""
        report = _compare(tmp_path, *product)
        assert _kinds(report, "libA.so") == []
        assert _kinds(report, "libB.so") != []


class TestUndocumentedExportAttribution:
    """Extension 2: an undocumented ``internal_c`` added to ``libB``."""

    @pytest.fixture
    def product(self, tmp_path: Path) -> tuple[Path, Path]:
        _require_toolchain()
        return (
            _build_product(tmp_path / "old"),
            _build_product(
                tmp_path / "new",
                src_b=_SRC_B + "int internal_c(int x) { return x * 3; }\n",
            ),
        )

    def test_exactly_one_finding_on_the_exporting_member(
        self, product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        report = _compare(tmp_path, *product)
        assert _kinds(report, "libB.so").count("exported_not_public") == 1
        assert "exported_not_public" not in _kinds(report, "libA.so")

    def test_the_release_accounting_attributes_it_to_that_member(
        self, product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """Attribution, stated as a relation rather than exact totals: only
        `libB` gained `internal_c`, so only `libB`'s undocumented count
        moves. Exact numbers would pin a toolchain's own baseline export set
        (a MinGW DLL auto-exports more than an ELF `.so` does), which is not
        what this asserts."""
        counts = _compare(tmp_path, *product)["public_surface_reconciliation"][
            "undocumented_exports_by_member"
        ]
        assert set(counts) == {"libA.so", "libB.so"}
        assert counts["libB.so"] >= counts["libA.so"] + 1

    def test_the_release_export_totals_add_up(
        self, product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """The partition identity, plus the two facts the fixture fixes: the
        header's two declarations are exported, and `internal_c` is not
        declared. The total itself is a toolchain property (see the
        attribution test above), so it is checked as a sum rather than a
        constant."""
        side = _compare(tmp_path, *product)["public_surface_reconciliation"]["sides"][
            "new"
        ]
        assert side["exports_declared_in_headers"] == 2
        assert side["exports_not_declared_in_headers"] >= 1
        assert (
            side["exports_total"]
            == side["exports_declared_in_headers"]
            + side["exports_not_declared_in_headers"]
        )


class TestPublicTypeChangeRenderedOnce:
    """Extension 3: one changed public type, one finding set."""

    @pytest.fixture
    def product(self, tmp_path: Path) -> tuple[Path, Path]:
        _require_toolchain()
        return (
            _build_product(tmp_path / "old"),
            _build_product(tmp_path / "new", extra_field="int b;"),
        )

    def test_the_type_change_is_reported_once_not_once_per_dso(
        self, product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """Selects the `Cfg` findings rather than asserting the shared set
        contains nothing else: on macOS the two dylibs also share a real
        `LC_ID_DYLIB` install-name change, which is a *correct* product-level
        fold and none of this test's business. The claim under test is the
        cardinality -- one entry per distinct fact, naming every affected
        library, rather than one per DSO."""
        section = _compare(tmp_path, *product)["public_surface_reconciliation"]
        cfg = [f for f in section["shared_findings"] if f["symbol"] == "Cfg"]
        assert cfg, section["shared_findings"]
        assert len({f["kind"] for f in cfg}) == len(cfg), cfg
        for finding in cfg:
            assert finding["affected_libraries"] == ["libA.so", "libB.so"]

    def test_the_per_library_tables_carry_no_clone(
        self, product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """No member table repeats a fact the release section already
        states. Asserted against the promoted set rather than emptiness, so
        a platform that legitimately gives one member its *own* finding
        (which by definition is not shared, and stays where it belongs)
        does not read as a clone."""
        report = _compare(tmp_path, *product)
        promoted = {
            (f["kind"], f["symbol"])
            for f in report["public_surface_reconciliation"]["shared_findings"]
        }
        for entry in report["libraries"]:
            for finding in entry.get("findings", []):
                assert (finding["kind"], finding["symbol"]) not in promoted

    def test_each_member_discloses_what_was_folded(
        self, product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """A shorter ``findings`` list next to a nonzero count is never
        silent about why."""
        report = _compare(tmp_path, *product)
        for entry in report["libraries"]:
            assert entry["product_level_findings"] >= 1

    def test_the_verdict_is_unchanged_by_the_fold(
        self, product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """The fold is a reporting de-duplication: it must not weaken the
        gate. A field added to a public struct is an ABI break here."""
        report = _compare(tmp_path, *product)
        assert report["verdict"] == "BREAKING"
        assert report["exit"]["code"] == 4

    def test_two_distinct_header_surfaces_acquire_one_each(
        self, product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """Scenario 8: old and new headers genuinely differ, so each side
        gets its own acquisition -- and its own key."""
        section = _compare(tmp_path, *product)["public_surface_reconciliation"]
        assert section["acquisition"]["acquisitions"] == 2
        keys = {side["acquisition_key"] for side in section["sides"].values()}
        assert len(keys) == 2


class TestUnrelatedMembersDoNotMultiplyFindings:
    """Scenario 17, end to end: a third library that shares the surface but
    changes nothing must not multiply the release's reported evidence."""

    def test_adding_a_member_does_not_grow_the_finding_count(
        self, tmp_path: Path
    ) -> None:
        _require_toolchain()
        two_old = _build_product(tmp_path / "two_old")
        two_new = _build_product(tmp_path / "two_new", extra_field="int b;")
        three_old = _build_product(tmp_path / "three_old")
        three_new = _build_product(tmp_path / "three_new", extra_field="int b;")
        for root in (three_old, three_new):
            src = root / "c.c"
            src.write_text(
                '#include "product.h"\nint api_a_alias(struct Cfg* c){return c->a;}\n',
                encoding="utf-8",
            )
            subprocess.run(
                [
                    "gcc",
                    "-shared",
                    "-fPIC",
                    "-o",
                    str(root / "lib" / "libC.so"),
                    str(src),
                    f"-I{root / 'include'}",
                ],
                check=True,
                capture_output=True,
            )
        two = _compare(tmp_path, two_old, two_new)["public_surface_reconciliation"]
        three = _compare(tmp_path, three_old, three_new)[
            "public_surface_reconciliation"
        ]
        assert len(three["shared_findings"]) == len(two["shared_findings"])
        assert len(three["missing_exports"]) == len(two["missing_exports"])


class TestAConditionalDeclarationStaysInTheContract:
    """The reported input, end to end: a declaration behind ``#ifdef
    FEATURE`` with ``compile.defines: [FEATURE]`` configured.

    Before the fix the release-level acquisition hashed the resolved compile
    context into its key but parsed without it, so this declaration never
    entered the product's contract at all: the two-library release saw 2
    obligations instead of 3, reported no missing export, and exited 0 --
    while the same product as a single member reported
    ``public_not_exported: api_c`` and exited 2 (Codex security review, PR
    #1328). The class-level guard lives in
    ``tests/test_release_public_surface.py``'s
    ``TestTheResolvedCompileContextReachesTheParse``; this is the
    real-toolchain witness.
    """

    _HEADER = """\
#pragma once
#ifdef __cplusplus
extern "C" {
#endif
int api_a(int x);
int api_b(int x);
#ifdef FEATURE
int api_c(int x);
#endif
#ifdef __cplusplus
}
#endif
"""

    def _product(self, root: Path) -> Path:
        root = root.resolve()
        include = root / "include"
        lib = root / "lib"
        include.mkdir(parents=True)
        lib.mkdir(parents=True)
        (include / "product.h").write_text(self._HEADER, encoding="utf-8")
        for name, body in (("a", "api_a"), ("b", "api_b")):
            src = root / f"{name}.c"
            src.write_text(
                f'#include "product.h"\nint {body}(int x) {{ return x; }}\n',
                encoding="utf-8",
            )
            subprocess.run(
                [
                    "gcc",
                    "-shared",
                    "-fPIC",
                    "-o",
                    str(lib / f"lib{name.upper()}.so"),
                    str(src),
                    f"-I{include}",
                ],
                check=True,
                capture_output=True,
            )
        return root

    @pytest.fixture
    def configured_product(self, tmp_path: Path) -> tuple[Path, Path]:
        _require_toolchain()
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  lang: c\n  defines:\n    - FEATURE\n", encoding="utf-8"
        )
        return self._product(tmp_path / "old"), self._product(tmp_path / "new")

    @staticmethod
    def _compare_in(cwd: Path, old: Path, new: Path) -> dict:
        """Run `compare` with *cwd* as the working directory, which is how
        the `.abicheck.yml` beside the fixture is discovered at all."""
        out = cwd / "report.json"
        previous = Path.cwd()
        os.chdir(cwd)
        try:
            result = CliRunner().invoke(
                main,
                [
                    "compare",
                    str(old / "lib"),
                    str(new / "lib"),
                    "--header",
                    f"old={old / 'include'}",
                    "--header",
                    f"new={new / 'include'}",
                    "-o",
                    f"json={out}",
                ],
            )
        finally:
            os.chdir(previous)
        assert out.exists(), result.output
        return json.loads(out.read_text(encoding="utf-8"))

    def test_the_guarded_declaration_is_part_of_the_contract(
        self, configured_product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        report = self._compare_in(tmp_path, *configured_product)
        side = report["public_surface_reconciliation"]["sides"]["new"]
        assert side["public_declarations_with_export_obligation"] == 3

    def test_its_missing_export_is_reported_once_at_release_level(
        self, configured_product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        report = self._compare_in(tmp_path, *configured_product)
        missing = report["public_surface_reconciliation"]["missing_exports"]
        assert [f["symbol"] for f in missing] == ["api_c"]

    def test_the_declarations_a_sibling_provides_are_still_satisfied(
        self, configured_product: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """The fix must not reintroduce the Cartesian product: ``api_a`` and
        ``api_b`` are each exported by one member and stay satisfied."""
        side = self._compare_in(tmp_path, *configured_product)[
            "public_surface_reconciliation"
        ]["sides"]["new"]
        assert side["satisfied_by_bundle_exports"] == 2
        assert [f["symbol"] for f in side["missing_from_bundle"]] == ["api_c"]
