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

"""``--artifact-set`` provider-ownership semantics (PR H, CLI cleanup phase
two, ADR-056 D2): the audit-mode duplicate-provider detector and the
opt-in expected-provider ownership manifest, plus their ``audit_bundle()``
wiring. Split out of ``tests/test_bundle.py`` rather than added there:
that module is a ``no_growth``-debt-tracked file (``architecture/
debt.yaml``), so new coverage lives here instead of raising its line-count
baseline.

Uses the identical minimal in-memory ``ElfMetadata`` fixture builders
``tests/test_bundle.py`` uses (no gcc/castxml needed) -- duplicated here
rather than imported cross-module, the same pattern ``tests/
test_scan_artifact_set_coverage.py`` already established for its own
``no_growth``-debt-tracked sibling.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.bundle import (
    BundleSnapshot,
    InstantiationManifest,
    ManifestEntry,
    _compute_resolution_graph,
)
from abicheck.checker_policy import ChangeKind
from abicheck.elf_metadata import ElfMetadata, ElfSymbol


def _meta(*, soname: str = "", exports: list[str] | None = None) -> ElfMetadata:
    """Minimal ``ElfMetadata`` -- exports only, this file's detectors need
    nothing else (no imports/needed, unlike ``test_bundle.py``'s fuller
    builder)."""
    syms = [ElfSymbol(name=n, visibility="default") for n in exports or []]
    return ElfMetadata(soname=soname or "", symbols=syms)


def _snapshot(libraries: dict[str, ElfMetadata]) -> BundleSnapshot:
    libs = {name: Path(f"/fake/{name}") for name in libraries}
    graph = _compute_resolution_graph(libs, libraries)
    return BundleSnapshot(
        root=Path("/fake"), libraries=libs, metadata=libraries, resolution=graph
    )


class TestDetectDuplicateProviders:
    """`_detect_duplicate_providers` -- audit-mode ownership-ambiguity
    detector: the same default-exported symbol name provided by 2+ set
    members."""

    def _detect(self, snapshot: BundleSnapshot):
        from abicheck.bundle_detectors import _detect_duplicate_providers

        return _detect_duplicate_providers(snapshot)

    def test_detects_duplicate_default_export(self) -> None:
        new = _snapshot(
            {
                "liba.so": _meta(soname="liba.so.1", exports=["shared_fn"]),
                "libb.so": _meta(soname="libb.so.1", exports=["shared_fn"]),
            }
        )
        findings = self._detect(new)
        assert len(findings) == 1
        assert findings[0].kind == ChangeKind.BUNDLE_DUPLICATE_PROVIDER
        assert findings[0].symbol == "shared_fn"
        assert findings[0].affected_libraries == ["liba.so", "libb.so"]

    def test_single_provider_no_finding(self) -> None:
        new = _snapshot(
            {
                "liba.so": _meta(soname="liba.so.1", exports=["only_here"]),
                "libb.so": _meta(soname="libb.so.1", exports=["something_else"]),
            }
        )
        assert self._detect(new) == []

    def test_nondefault_duplicate_not_flagged(self) -> None:
        # Both providers export the symbol, but only as a non-default
        # (@specific) versioned definition -- no unversioned reference can
        # actually resolve ambiguously against them.
        liba = _meta(soname="liba.so.1")
        liba.symbols.append(
            ElfSymbol(
                name="foo", visibility="default", version="V1", is_default=False
            )
        )
        libb = _meta(soname="libb.so.1")
        libb.symbols.append(
            ElfSymbol(
                name="foo", visibility="default", version="V1", is_default=False
            )
        )
        new = _snapshot({"liba.so": liba, "libb.so": libb})
        assert self._detect(new) == []

    def test_linker_synthesized_symbols_excluded(self) -> None:
        new = _snapshot(
            {
                "liba.so": _meta(soname="liba.so.1", exports=["_edata", "_end"]),
                "libb.so": _meta(soname="libb.so.1", exports=["_edata", "_end"]),
            }
        )
        assert self._detect(new) == []

    def test_system_shaped_symbol_excluded(self) -> None:
        new = _snapshot(
            {
                "liba.so": _meta(soname="liba.so.1", exports=["_ZNSt6vectorIiEE"]),
                "libb.so": _meta(soname="libb.so.1", exports=["_ZNSt6vectorIiEE"]),
            }
        )
        assert self._detect(new) == []


class TestDetectManifestOwnership:
    """`_detect_manifest_ownership` -- audit-mode sibling of `compare
    --manifest`'s two-sided drift check, applied single-sided against one
    declared set."""

    def _detect(self, snapshot: BundleSnapshot, manifest: InstantiationManifest):
        from abicheck.bundle_detectors import _detect_manifest_ownership

        return _detect_manifest_ownership(snapshot, manifest)

    def test_missing_symbol_flagged(self) -> None:
        new = _snapshot({"liba.so": _meta(soname="liba.so.1", exports=["core_add"])})
        manifest = InstantiationManifest(
            entries=(ManifestEntry(symbol="promised_but_absent"),)
        )
        findings = self._detect(new, manifest)
        assert len(findings) == 1
        assert findings[0].kind == ChangeKind.BUNDLE_MANIFEST_ENTRY_UNSATISFIED
        assert findings[0].symbol == "promised_but_absent"

    def test_wrong_provider_flagged(self) -> None:
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1", exports=["shared_util"]),
                "libutil.so": _meta(soname="libutil.so.1"),
            }
        )
        manifest = InstantiationManifest(
            entries=(
                ManifestEntry(
                    symbol="shared_util",
                    library="libutil.so",
                    optional_provider=False,
                ),
            )
        )
        findings = self._detect(new, manifest)
        assert len(findings) == 1
        assert findings[0].kind == ChangeKind.BUNDLE_MANIFEST_ENTRY_UNSATISFIED
        assert findings[0].new_value == "libcore.so"

    def test_expected_provider_satisfied_no_finding(self) -> None:
        new = _snapshot(
            {
                "libcore.so": _meta(soname="libcore.so.1"),
                "libutil.so": _meta(soname="libutil.so.1", exports=["shared_util"]),
            }
        )
        manifest = InstantiationManifest(
            entries=(
                ManifestEntry(
                    symbol="shared_util",
                    library="libutil.so",
                    optional_provider=False,
                ),
            )
        )
        assert self._detect(new, manifest) == []

    def test_optional_provider_any_sibling_satisfies(self) -> None:
        new = _snapshot(
            {"libcore.so": _meta(soname="libcore.so.1", exports=["shared_util"])}
        )
        manifest = InstantiationManifest(
            entries=(ManifestEntry(symbol="shared_util"),)
        )
        assert self._detect(new, manifest) == []


class TestAuditBundleWiring:
    """`audit_bundle()` wires in the PR H audit-mode detectors: duplicate-
    provider detection unconditionally, manifest ownership only when a
    manifest is given."""

    def test_duplicate_provider_included_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.bundle as bundle_mod
        from abicheck.bundle import audit_bundle

        libraries = {"liba.so": Path("liba.so"), "libb.so": Path("libb.so")}

        def _fake_snapshot(libs):
            return _snapshot(
                {
                    "liba.so": _meta(soname="liba.so.1", exports=["dup"]),
                    "libb.so": _meta(soname="libb.so.1", exports=["dup"]),
                }
            )

        monkeypatch.setattr(bundle_mod, "build_bundle_snapshot", _fake_snapshot)
        result = audit_bundle(libraries)
        assert any(
            f.kind == ChangeKind.BUNDLE_DUPLICATE_PROVIDER for f in result.findings
        )

    def test_manifest_ownership_wired_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.bundle as bundle_mod
        from abicheck.bundle import audit_bundle

        libraries = {"liba.so": Path("liba.so")}

        def _fake_snapshot(libs):
            return _snapshot({"liba.so": _meta(soname="liba.so.1")})

        monkeypatch.setattr(bundle_mod, "build_bundle_snapshot", _fake_snapshot)
        manifest = InstantiationManifest(
            entries=(ManifestEntry(symbol="never_exported"),)
        )
        result = audit_bundle(libraries, manifest=manifest)
        assert any(
            f.kind == ChangeKind.BUNDLE_MANIFEST_ENTRY_UNSATISFIED
            for f in result.findings
        )

    def test_no_manifest_no_ownership_findings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.bundle as bundle_mod
        from abicheck.bundle import audit_bundle

        libraries = {"liba.so": Path("liba.so")}

        def _fake_snapshot(libs):
            return _snapshot({"liba.so": _meta(soname="liba.so.1")})

        monkeypatch.setattr(bundle_mod, "build_bundle_snapshot", _fake_snapshot)
        result = audit_bundle(libraries)
        assert result.findings == []
