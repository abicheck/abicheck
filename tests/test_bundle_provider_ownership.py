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
    load_manifest,
)
from abicheck.checker_policy import ChangeKind
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding


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

    def test_weak_comdat_duplicate_not_flagged(self) -> None:
        # Codex review, fresh evidence: ordinary C++ vague linkage (the
        # same inline function / template instantiation compiled into 2+
        # DSOs) emits an identical STB_WEAK COMDAT copy in each -- expected,
        # deduplicated by the dynamic linker at load time, not an ownership
        # conflict.
        liba = _meta(soname="liba.so.1")
        liba.symbols.append(
            ElfSymbol(name="_ZN3Foo6inlineEv", binding=SymbolBinding.WEAK)
        )
        libb = _meta(soname="libb.so.1")
        libb.symbols.append(
            ElfSymbol(name="_ZN3Foo6inlineEv", binding=SymbolBinding.WEAK)
        )
        new = _snapshot({"liba.so": liba, "libb.so": libb})
        assert self._detect(new) == []

    def test_one_strong_one_weak_not_flagged(self) -> None:
        # A single strong provider alongside a weak COMDAT copy elsewhere is
        # still just one real owner -- only 2+ *strong* providers of the
        # same symbol name a genuine, unresolved conflict.
        liba = _meta(soname="liba.so.1")
        liba.symbols.append(ElfSymbol(name="foo", binding=SymbolBinding.GLOBAL))
        libb = _meta(soname="libb.so.1")
        libb.symbols.append(ElfSymbol(name="foo", binding=SymbolBinding.WEAK))
        new = _snapshot({"liba.so": liba, "libb.so": libb})
        assert self._detect(new) == []

    def test_two_strong_providers_flagged_even_with_a_third_weak_one(self) -> None:
        liba = _meta(soname="liba.so.1")
        liba.symbols.append(ElfSymbol(name="foo", binding=SymbolBinding.GLOBAL))
        libb = _meta(soname="libb.so.1")
        libb.symbols.append(ElfSymbol(name="foo", binding=SymbolBinding.GLOBAL))
        libc = _meta(soname="libc.so.1")
        libc.symbols.append(ElfSymbol(name="foo", binding=SymbolBinding.WEAK))
        new = _snapshot({"liba.so": liba, "libb.so": libb, "libc.so": libc})
        findings = self._detect(new)
        assert len(findings) == 1
        assert findings[0].affected_libraries == ["liba.so", "libb.so"]

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

    def test_nondefault_only_export_does_not_satisfy_promise(self) -> None:
        # Security P1 (Codex review, fresh evidence): a symbol that exists
        # in .dynsym only as a non-default versioned definition (foo@V1,
        # never foo@@V1) cannot actually be linked against by an
        # unversioned consumer -- it must not silently satisfy a manifest
        # promise. A CLI reproduction before this fix returned COMPATIBLE,
        # zero bundle findings, exit 0, while `ld` failed to resolve `foo`.
        provider = _meta(soname="liba.so.1")
        provider.symbols.append(
            ElfSymbol(
                name="foo", visibility="default", version="V1", is_default=False
            )
        )
        new = _snapshot({"liba.so": provider})
        manifest = InstantiationManifest(entries=(ManifestEntry(symbol="foo"),))
        findings = self._detect(new, manifest)
        assert len(findings) == 1
        assert findings[0].kind == ChangeKind.BUNDLE_MANIFEST_ENTRY_UNSATISFIED
        assert findings[0].symbol == "foo"

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


class TestMatchEntryDeadlineCheckpoint:
    """`_match_entry`'s per-target loop calls `deadline.check()` (Codex
    review, PR H): a large pattern/template manifest scans the full
    demangled export index per target, and `deadline_scope()` alone does
    not interrupt pure Python work -- without a checkpoint here a small
    `--budget` could be overrun well before `run_scan_set`'s own elapsed-
    time check (after `audit_bundle` returns) ever sees it."""

    def test_raises_when_deadline_already_expired(self) -> None:
        from abicheck import deadline
        from abicheck.bundle import ManifestEntry
        from abicheck.bundle_detector_heuristics import _match_entry

        new = _snapshot({"liba.so": _meta(soname="liba.so.1", exports=["foo"])})
        entry = ManifestEntry(pattern="foo*")
        with deadline.deadline_scope(-1):
            with pytest.raises(deadline.DeadlineExceeded):
                _match_entry(entry, new)

    def test_no_deadline_is_a_no_op(self) -> None:
        from abicheck.bundle import ManifestEntry
        from abicheck.bundle_detector_heuristics import _match_entry

        new = _snapshot({"liba.so": _meta(soname="liba.so.1", exports=["foo"])})
        entry = ManifestEntry(symbol="foo")
        # No deadline_scope active -- must behave exactly as before.
        [(target, kind, matched, providers)] = _match_entry(entry, new)
        assert (target, kind, matched) == ("foo", "symbol", ["foo"])
        assert [p.library for p in providers] == ["liba.so"]

class TestIndexScanDeadlineCheckpoint:
    """Codex review, PR H, second round: `_match_entry`'s own per-target
    checkpoint only bounds time *between* targets -- a *single*
    pattern/template target scanning one large index, or building one
    large index in the first place, had no checkpoint of its own. Tests
    `_build_demangled_index`/`_match_target_against_index` directly
    (bypassing `_match_entry`'s outer checkpoint entirely) so only each
    function's own inner-loop checkpoint can be what raises here."""

    def test_build_demangled_index_checkpoints(self) -> None:
        from abicheck import deadline
        from abicheck.bundle_detector_heuristics import (
            _DEADLINE_CHECK_INTERVAL,
            _build_demangled_index,
        )

        exports = [f"sym_{i}" for i in range(_DEADLINE_CHECK_INTERVAL)]
        new = _snapshot({"liba.so": _meta(soname="liba.so.1", exports=exports)})
        with deadline.deadline_scope(-1):
            with pytest.raises(deadline.DeadlineExceeded):
                _build_demangled_index(new)

    def test_match_target_against_index_checkpoints(self) -> None:
        from abicheck import deadline
        from abicheck.bundle_detector_heuristics import (
            _match_target_against_index,
        )

        # index=None forces this call to build its own index, unbounded by
        # the caller's own deadline scope -- must still raise before
        # returning.
        new = _snapshot({"liba.so": _meta(soname="liba.so.1", exports=["foo"])})
        with deadline.deadline_scope(-1):
            with pytest.raises(deadline.DeadlineExceeded):
                _match_target_against_index("never_matches*", "pattern", new)


class TestLoadManifestRequiresLibraryForRequiredProvider:
    """`load_manifest` rejects `optional_provider: false` with no
    `library` (Codex review, security P2, PR H): every wrong-provider
    check downstream (`_manifest_ownership_findings`,
    `_detect_manifest_drift`) is itself gated on `entry.library is not
    None`, so accepting this shape silently degrades a declared
    *required*-provider promise into the always-permissive
    `optional_provider: true` default -- any matching library would
    satisfy it."""

    def test_rejects_required_provider_with_no_library(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text(
            '{"version": 1, "provides": ['
            '{"symbol": "foo", "optional_provider": false}'
            "]}",
        )
        with pytest.raises(ValueError, match="requires a 'library'"):
            load_manifest(path)

    def test_required_provider_with_library_still_loads(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text(
            '{"version": 1, "provides": ['
            '{"symbol": "foo", "library": "liba.so", "optional_provider": false}'
            "]}",
        )
        m = load_manifest(path)
        assert m.entries[0].library == "liba.so"
        assert m.entries[0].optional_provider is False

    def test_optional_provider_default_still_allows_no_library(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "manifest.json"
        path.write_text('{"version": 1, "provides": [{"symbol": "foo"}]}')
        m = load_manifest(path)
        assert m.entries[0].library is None
        assert m.entries[0].optional_provider is True
