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

"""Request-local L2 reuse on the stored-``BundleFacts``-vs-live route.

``bundle_side_input.compare_release_against_bundle_facts`` fans one stored
OLD capture out over a live NEW directory, resolving every matched member
through ``workflows.input_resolution.resolve_input``. Members of one release
routinely share a public header set and compile context, so without a
request-local acquisition table each member re-acquires, re-decodes and
re-normalizes the identical header AST, and the bundle-topology assembly
afterwards re-parses every already-resolved member's ELF a second time.

The invariants below are written against the *mechanism* rather than one
fixture's output, so a later change that quietly reintroduces per-member
work fails here rather than only showing up as wall time:

* acquisition and neutral-normalization work is counted per
  ``(backend, acquisition key)`` and per normalization scope -- never by
  intercepting every ``json.loads`` in the process, which would also count
  unrelated snapshot/policy reads as AST decodes;
* genuinely differing per-member compile/header context must produce
  *separate* keys, so reuse can never merge two contexts;
* a member with no resolved evidence still takes the documented live-parse
  fallback, and is never substituted with an empty success;
* a failing member leaves its siblings' completed comparisons intact and
  leaves no acquisition scope active behind it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from abicheck import bundle, dumper_cache
from abicheck.bundle_side_input import compare_release_against_bundle_facts
from abicheck.checker_policy import Verdict
from abicheck.compile_context import CompileContext
from abicheck.dumper_cache import ast_acquisition_active, ast_acquisition_scope
from abicheck.extract import header_ast_fields
from abicheck.serialization import save_bundle_facts
from abicheck.workflows.bundle_facts_capture import capture_bundle_facts
from abicheck.workflows.input_resolution import resolve_input

MEMBERS = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")
#: The member whose NEW build genuinely loses a public export.
BREAKING_MEMBER = "gamma"


# ---------------------------------------------------------------------------
# Real six-DSO C++ fixture: one shared public header, per-member exports
# ---------------------------------------------------------------------------


def _header_text(*, with_header_only_addition: bool) -> str:
    added_field = "  int c;\n" if with_header_only_addition else ""
    decls = "\n".join(
        f"struct Shape_{name} {{ int a; double b;\n{added_field}}};\n"
        f"void {name}_alpha(const Shape_{name}& s);\n"
        f"int {name}_beta(int x);\n"
        for name in MEMBERS
    )
    extra = (
        "\ninline int shared_header_only_added(int x) { return x + 7; }\n"
        if with_header_only_addition
        else ""
    )
    return "#pragma once\n" + decls + extra


def _source_text(name: str, *, drop_beta: bool) -> str:
    beta = "" if drop_beta else f"int {name}_beta(int x) {{ return x * 2; }}\n"
    return (
        '#include "api.h"\n'
        f"void {name}_alpha(const Shape_{name}& s) {{ (void)s; }}\n" + beta
    )


def _build_release(
    root: Path, *, drop_export: bool, header_only_addition: bool
) -> Path:
    """Compile six separately-built DSOs sharing one public header."""
    src = root / "src"
    out = root / "lib"
    src.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    (src / "api.h").write_text(
        _header_text(with_header_only_addition=header_only_addition)
    )
    for name in MEMBERS:
        cpp = src / f"{name}.cpp"
        cpp.write_text(
            _source_text(name, drop_beta=drop_export and name == BREAKING_MEMBER)
        )
        subprocess.run(
            [
                "g++",
                "-shared",
                "-fPIC",
                "-g",
                "-o",
                str(out / f"lib{name}.so"),
                str(cpp),
                f"-Wl,-soname,lib{name}.so",
            ],
            check=True,
            capture_output=True,
        )
    return out


def _capture_old_facts(tmp_path: Path, lib_dir: Path, header: Path) -> Path:
    """Produce the OLD BundleFacts the supported way: dump, then capture."""
    snapshots = {}
    paths = {}
    for name in MEMBERS:
        path = lib_dir / f"lib{name}.so"
        snapshots[f"lib{name}.so"] = resolve_input(path, headers=[header])
        paths[f"lib{name}.so"] = path
    facts_path = tmp_path / "old-facts.json"
    save_bundle_facts(capture_bundle_facts(snapshots, library_paths=paths), facts_path)
    return facts_path


# ---------------------------------------------------------------------------
# Counters: per (backend, acquisition key) and per normalization scope
# ---------------------------------------------------------------------------


class _AcquisitionCounters:
    """Count acquisition requests, real producer runs and ELF topology reads.

    Wraps the two real ``run_ast_acquisition`` call sites (``dumper`` reaches
    it as a module attribute, ``extract.header_ast_fields`` by a from-import,
    so both bindings are wrapped) and ``bundle.parse_elf_metadata``. The
    compiler, the AST parser, the normalizer and the comparison underneath
    stay entirely real -- only the call counts are observed.
    """

    def __init__(self) -> None:
        self.requests: Counter[tuple[str, str]] = Counter()
        self.producer_runs: Counter[tuple[str, str]] = Counter()
        self.topology_elf_reads: Counter[str] = Counter()

    @property
    def ast_keys(self) -> set[tuple[str, str]]:
        return {k for k in self.producer_runs if not k[0].endswith("-normalized")}

    @property
    def normalization_keys(self) -> set[tuple[str, str]]:
        return {k for k in self.producer_runs if k[0].endswith("-normalized")}

    def keys_per_backend(self, *, normalized: bool) -> dict[str, int]:
        """Distinct acquisition keys per backend.

        Counted per backend because ``header_backend="auto"`` legitimately
        runs both header-AST backends on a host that has both -- the claim
        under test is "one acquisition per distinct context", never "one
        acquisition overall".
        """
        source = self.normalization_keys if normalized else self.ast_keys
        counts: Counter[str] = Counter()
        for backend, _key in source:
            counts[backend] += 1
        return dict(counts)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_run = dumper_cache.run_ast_acquisition
        real_elf = bundle.parse_elf_metadata

        def wrapped(backend, key, producer):
            self.requests[(backend, key)] += 1

            def counted_producer():
                self.producer_runs[(backend, key)] += 1
                return producer()

            return real_run(backend, key, counted_producer)

        def wrapped_elf(path, *args, **kwargs):
            self.topology_elf_reads[Path(path).name] += 1
            return real_elf(path, *args, **kwargs)

        monkeypatch.setattr(dumper_cache, "run_ast_acquisition", wrapped)
        monkeypatch.setattr(header_ast_fields, "run_ast_acquisition", wrapped)
        monkeypatch.setattr(bundle, "parse_elf_metadata", wrapped_elf)


def _canonical_findings(result) -> list[tuple]:
    """Full per-member + cross-library findings, not a displayed table."""
    per_library: list[tuple] = [
        (
            diff.library,
            change.kind.value,
            change.symbol,
            change.qualified_name,
            change.description,
            change.old_value,
            change.new_value,
        )
        for diff in result.per_library
        for change in diff.changes
    ]
    cross = [
        (
            f.kind.value if hasattr(f.kind, "value") else str(f.kind),
            f.symbol,
            f.description,
            f.consumer_library,
            f.provider_library,
        )
        for f in result.bundle_findings
    ]
    return sorted(per_library) + sorted(cross)


# ---------------------------------------------------------------------------
# Pure-Python invariants (no toolchain needed)
# ---------------------------------------------------------------------------


def test_scope_contract_is_reused_not_shadowed() -> None:
    """A nested scope yields the outer table -- the contract this fix relies on."""
    with ast_acquisition_scope() as outer:
        with ast_acquisition_scope() as inner:
            assert inner is outer
        assert ast_acquisition_active()
    assert not ast_acquisition_active()


def test_scope_is_released_on_error_and_deadline_exit() -> None:
    """No acquisition scope survives an exception leaving the workflow."""
    for exc_type in (RuntimeError, KeyboardInterrupt):
        with pytest.raises(exc_type):
            with ast_acquisition_scope():
                assert ast_acquisition_active()
                raise exc_type("boom")
        assert not ast_acquisition_active()


def test_missing_evidence_falls_back_to_a_live_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member with no resolved evidence is parsed live, never emptied.

    States the fallback half of the builder contract directly on the shared
    primitive, with several independently-chosen evidence shapes rather than
    only the one the integration fixture happens to produce.
    """
    from abicheck.bundle_models import BundleSignatureEvidence
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol

    def _evidence(name: str) -> BundleSignatureEvidence:
        return BundleSignatureEvidence(
            function_map={},
            variable_map={},
            elf_only_mode=False,
            elf=ElfMetadata(
                soname=name,
                needed=[],
                symbols=[ElfSymbol(name=f"{name}_sym", visibility="default")],
                imports=[],
            ),
            library_filename=name,
        )

    parsed: list[str] = []

    def fake_build(libraries):
        parsed.extend(sorted(libraries))
        return bundle.build_bundle_snapshot_from_metadata(
            {
                name: ElfMetadata(
                    soname=name,
                    needed=[],
                    symbols=[ElfSymbol(name=f"{name}_sym", visibility="default")],
                    imports=[],
                )
                for name in libraries
            },
            paths=dict(libraries),
        )

    monkeypatch.setattr(bundle, "build_bundle_snapshot", fake_build)

    names = [f"lib{i}.so" for i in range(6)]
    libraries = {name: Path(f"/fake/{name}") for name in names}
    # Several independently-chosen partitions, not just the reported one.
    for resolved_names in ([], names[:1], names[:3], names[:-1], names):
        parsed.clear()
        snap = bundle.build_bundle_snapshot_mixed(
            libraries,
            resolved_evidence={n: _evidence(n) for n in resolved_names},
        )
        # Exactly the unresolved remainder took the live-parse fallback ...
        assert parsed == sorted(set(names) - set(resolved_names))
        # ... and every member still reached the graph with real metadata:
        # "not resolved" is never an empty success.
        assert set(snap.metadata) == set(names)
        for name in names:
            assert snap.metadata[name].symbols


# ---------------------------------------------------------------------------
# Real end-to-end route (real compiler, parser, normalizer, comparison)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def release(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Build the six-DSO OLD/NEW release once for the whole module.

    Module-scoped so the compile cost is paid once, and shares only the
    *inputs* (the built binaries and the stored OLD capture) -- never a
    comparison result, which is what each test independently produces.
    """
    if shutil.which("g++") is None or sys.platform != "linux":
        pytest.skip("needs g++ and ELF")
    root = tmp_path_factory.mktemp("release")
    old_dir = _build_release(
        root / "old", drop_export=False, header_only_addition=False
    )
    new_dir = _build_release(root / "new", drop_export=True, header_only_addition=True)
    facts = _capture_old_facts(root, old_dir, root / "old" / "src" / "api.h")
    return {
        "facts": facts,
        "new_dir": new_dir,
        "new_header": root / "new" / "src" / "api.h",
        "root": root,
    }


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("g++") is None or sys.platform != "linux",
    reason="needs g++ and ELF",
)
class TestBundleFactsLiveRouteReuse:
    def _run(self, release: dict, counters: _AcquisitionCounters, **kwargs):
        return compare_release_against_bundle_facts(
            release["facts"],
            release["new_dir"],
            headers=[release["new_header"]],
            **kwargs,
        )

    def test_one_shared_context_acquires_and_normalizes_once(
        self, release: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Six members sharing one header/compile context do one acquisition."""
        counters = _AcquisitionCounters()
        counters.install(monkeypatch)
        result = self._run(release, counters)

        assert len(result.per_library) == len(MEMBERS)
        # The mechanism actually engaged (a vacuous zero would pass a
        # "not more than one" assertion just as well).
        assert counters.requests, "no acquisition was coordinated at all"
        # One key per backend, not one per member.
        for backend, key in counters.ast_keys:
            assert counters.producer_runs[(backend, key)] == 1
            assert counters.requests[(backend, key)] >= 1
        # One key per backend, independent of how many members ran: this is
        # the number that read len(MEMBERS) before request-local reuse.
        per_backend = counters.keys_per_backend(normalized=False)
        assert per_backend, "no header-AST acquisition observed"
        assert all(n == 1 for n in per_backend.values()), per_backend
        # Neutral normalization is counted in its own scope, separately --
        # never folded into the AST-decode number above.
        for scope_key in counters.normalization_keys:
            assert counters.producer_runs[scope_key] == 1
        normalized_per_backend = counters.keys_per_backend(normalized=True)
        assert all(n == 1 for n in normalized_per_backend.values()), (
            normalized_per_backend
        )

    def test_no_topology_elf_reparse_for_resolved_members(
        self, release: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bundle assembly reuses resolved evidence instead of re-reading ELF."""
        counters = _AcquisitionCounters()
        counters.install(monkeypatch)
        result = self._run(release, counters)

        compared = {d.library for d in result.per_library}
        assert len(compared) == len(MEMBERS)
        for name in compared:
            assert counters.topology_elf_reads[name] == 0, (
                f"{name} was re-parsed during bundle topology assembly"
            )

    def test_differing_compile_context_is_not_merged(
        self, release: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Members with a genuinely different macro set get separate keys."""
        counters = _AcquisitionCounters()
        counters.install(monkeypatch)
        split = {
            f"lib{name}.so": CompileContext(gcc_options="-DABICHECK_VARIANT_B=1")
            for name in MEMBERS[3:]
        }
        result = self._run(release, counters, per_library_compile=split)

        # The three members given a different macro set resolve under their
        # own context and are then recorded as not-comparable against a
        # capture that declared no such macro (ADR-050 D2) -- never silently
        # folded onto the other three members' AST.
        assert len(result.per_library) == len(MEMBERS) - len(split)
        assert set(result.not_comparable_members) == set(split)
        per_backend = counters.keys_per_backend(normalized=False)
        assert per_backend, "no header-AST acquisition observed"
        # Two genuinely different contexts -> two keys per backend, and each
        # still acquired exactly once: reuse never merges two contexts.
        assert all(n == 2 for n in per_backend.values()), per_backend
        for key in counters.ast_keys:
            assert counters.producer_runs[key] == 1

    def test_real_break_and_header_only_evidence_survive(
        self, release: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The genuine export loss is still reported; header-only facts stay."""
        counters = _AcquisitionCounters()
        counters.install(monkeypatch)
        result = self._run(release, counters)

        by_library = {d.library: d for d in result.per_library}
        broken = by_library[f"lib{BREAKING_MEMBER}.so"]
        assert broken.verdict in (Verdict.BREAKING, Verdict.API_BREAK)
        assert any(
            f"{BREAKING_MEMBER}_beta" in (c.symbol or "") for c in broken.changes
        ), [c.symbol for c in broken.changes]
        # The header-only addition is visible somewhere in the full result --
        # read off the canonical findings, never a truncated display table.
        findings = _canonical_findings(result)
        # A public struct gained a field on NEW: pure header (L2) evidence,
        # with no export-table change anywhere to carry it. Losing it would
        # mean the shared acquisition published one member's view to all.
        assert any(
            "Shape_" in str(f) or "shared_header_only_added" in str(f) for f in findings
        ), findings
        # ... and it is observed for *every* member, not just the first one
        # to acquire the shared AST.
        libraries_with_layout_evidence = {
            f[0] for f in findings if str(f[0]).startswith("lib") and "Shape_" in str(f)
        }
        assert len(libraries_with_layout_evidence) == len(MEMBERS), (
            libraries_with_layout_evidence
        )

    def test_a_failing_member_does_not_destroy_its_siblings(
        self, release: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """One unreadable NEW member is recorded; the rest still compare."""
        broken_dir = tmp_path / "lib"
        shutil.copytree(release["new_dir"], broken_dir)
        target = broken_dir / f"lib{MEMBERS[-1]}.so"
        target.write_bytes(b"\x7fELF" + b"\x00" * 64)

        counters = _AcquisitionCounters()
        counters.install(monkeypatch)
        result = compare_release_against_bundle_facts(
            release["facts"],
            broken_dir,
            headers=[release["new_header"]],
        )

        compared = {d.library for d in result.per_library}
        assert len(compared) >= len(MEMBERS) - 1
        # Never a successful empty member for the damaged artifact.
        assert f"lib{MEMBERS[-1]}.so" not in compared or any(
            d.changes for d in result.per_library if d.library == f"lib{MEMBERS[-1]}.so"
        )
        # And the workflow left no scope behind on the way out.
        assert not ast_acquisition_active()
