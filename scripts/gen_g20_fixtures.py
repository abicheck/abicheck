#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate the committed snapshot fixtures for the G20 example cases (143–151).

The G20 corpus (ADR-035 / plan ``g20-source-scan-example-catalog``) demonstrates
the single-release *audit* and intra-version *cross-source* machinery, neither of
which fits the catalog's usual ``v1``/``v2`` binary-diff shape. Each case ships a
hand-built :class:`~abicheck.model.AbiSnapshot` serialized to
``examples/caseNN_*/snapshot.abi.json`` (plus ``thin.abi.json`` for the
provider-matrix case). ``tests/test_g20_catalog.py`` loads each fixture and runs
``run_crosschecks`` against the case's ``expected_crosscheck_kinds`` /
``expected_providers`` in ``ground_truth.json`` — so the corpus is validated in
the fast lane with **no compiler / castxml** (the snapshots already carry the
provenance that a live L2 dump would otherwise produce).

Run after changing a fixture's design::

    python scripts/gen_g20_fixtures.py

This writer is the single source of truth for the fixtures' content; ``--check``
fails if the committed snapshots drift from what it would produce.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path

from abicheck.buildsource.build_evidence import BuildEvidence, BuildOption
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_abi import SourceAbiSurface
from abicheck.buildsource.source_graph import SourceGraphSummary
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Param, RecordType, ScopeOrigin
from abicheck.serialization import snapshot_to_dict

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def _snap(**kw) -> AbiSnapshot:
    kw.setdefault("library", "libdemo.so")
    kw.setdefault("version", "1.0")
    kw.setdefault("from_headers", True)
    return AbiSnapshot(**kw)


def _elf(*syms: ElfSymbol, **kw) -> ElfMetadata:
    return ElfMetadata(symbols=list(syms), **kw)


# ── case143: accidental export (exported_not_public) ─────────────────────────
def case143_audit_accidental_export() -> AbiSnapshot:
    snap = _snap(
        elf=_elf(ElfSymbol(name="_Z6renderv"), ElfSymbol(name="_Z11debug_dumpv"))
    )
    snap.functions = [
        Function(
            name="render",
            mangled="_Z6renderv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
        # Defined with default visibility but never declared in a public header:
        # an accidental export. Only binary-exports ↔ public-header-AST sees it.
        Function(
            name="debug_dump",
            mangled="_Z11debug_dumpv",
            return_type="void",
            origin=ScopeOrigin.EXPORT_ONLY,
        ),
    ]
    return snap


# ── case144: private header leak (private_header_leak) ───────────────────────
def case144_audit_private_header_leak() -> AbiSnapshot:
    snap = _snap(elf=_elf(ElfSymbol(name="_Z11make_widgetv")))
    snap.functions = [
        Function(
            name="make_widget",
            mangled="_Z11make_widgetv",
            return_type="detail::WidgetImpl *",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(
            name="detail::WidgetImpl",
            kind="struct",
            origin=ScopeOrigin.PRIVATE_HEADER,
        ),
    ]
    return snap


# ── case145: unversioned export under a versioning scheme ────────────────────
def case145_audit_unversioned_export() -> AbiSnapshot:
    elf = _elf(
        ElfSymbol(name="demo_init", version="DEMO_1.0", is_default=True),
        ElfSymbol(name="demo_run", version="DEMO_1.0", is_default=True),
        # New export shipped with no version node though a scheme exists.
        ElfSymbol(name="demo_experimental", version=None, is_default=True),
        versions_defined=["DEMO_1.0"],
    )
    return _snap(elf=elf)


# ── case146: RTTI exported for an internal type ──────────────────────────────
def case146_audit_rtti_for_internal() -> AbiSnapshot:
    snap = _snap(
        elf=_elf(
            ElfSymbol(name="_Z6renderv"),
            ElfSymbol(name="_ZTI12InternalNode"),
            ElfSymbol(name="_ZTV12InternalNode"),
        )
    )
    snap.functions = [
        Function(
            name="render",
            mangled="_Z6renderv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(
            name="InternalNode", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER
        ),
    ]
    return snap


# ── case147: depth ladder — S5 replay confirms a private-header leak ─────────
def case147_scan_depth_ladder() -> AbiSnapshot:
    # The S3 pattern scan flags a risky construct; the S5 source graph + replay
    # confirm a genuine private_header_leak (adds the source_index provider). The
    # README shows what each depth proved (S3 lexical → S5 semantic).
    snap = _snap(elf=_elf(ElfSymbol(name="_Z7connectv")))
    snap.functions = [
        Function(
            name="connect",
            mangled="_Z7connectv",
            return_type="void",
            params=[Param(name="s", type="detail::SessionState &")],
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(
            name="detail::SessionState",
            kind="struct",
            origin=ScopeOrigin.PRIVATE_HEADER,
        ),
    ]
    snap.build_source = BuildSourcePack(root="", source_graph=SourceGraphSummary())
    return snap


# ── case148: header build-context mismatch (L2 macros ↔ L3 flags) ────────────
def case148_xcheck_header_build_mismatch() -> AbiSnapshot:
    be = BuildEvidence(
        build_options=[
            BuildOption(key="glibcxx_use_cxx11_abi", value="1", abi_relevant=True),
            BuildOption(key="define:BIG_BUFFERS", value="1", abi_relevant=True),
        ]
    )
    snap = _snap(build_source=BuildSourcePack(root="", build_evidence=be))
    # Headers were parsed without the build's ABI-relevant flags → the recorded
    # layout cannot be trusted. Only L2-macros ↔ L3-flags exposes this.
    snap.parsed_with_build_context = False
    return snap


# ── case149: ODR type variant (L4 layout ↔ layout) ───────────────────────────
def case149_xcheck_odr_variant() -> AbiSnapshot:
    surface = SourceAbiSurface(
        odr_conflicts=[
            {
                "qualified_name": "geometry::Vec3",
                "header": "include/geometry/vec3.h",
                "old_type_hash": "sha256:aaa",
                "new_type_hash": "sha256:bbb",
            }
        ]
    )
    return _snap(build_source=BuildSourcePack(root="", source_abi=surface))


# ── case150: bidirectional export ↔ decl pair ────────────────────────────────
def case150_xcheck_export_public_pair() -> AbiSnapshot:
    snap = _snap(elf=_elf(ElfSymbol(name="_Z8internalv", is_default=True)))
    snap.functions = [
        # Exported with no public declaration → exported_not_public.
        Function(
            name="internal",
            mangled="_Z8internalv",
            return_type="void",
            origin=ScopeOrigin.EXPORT_ONLY,
        ),
        # Declared in a public header but the binary never exports it (a static
        # definition slipped in) → public_not_exported.
        Function(
            name="public_api",
            mangled="_Z10public_apiv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
            source_location="include/demo/api.h:12",
        ),
    ]
    return snap


# ── case151: provider-agreement matrix (rich ↔ thin corroboration) ───────────
def case151_xcheck_provider_matrix() -> AbiSnapshot:
    # Rich variant: header-AST provenance PLUS an L5 source graph, so the
    # private_header_leak finding is corroborated by two providers
    # (public_header_ast + source_index).
    snap = case144_audit_private_header_leak()
    snap.build_source = BuildSourcePack(root="", source_graph=SourceGraphSummary())
    return snap


def case151_xcheck_provider_matrix_thin() -> AbiSnapshot:
    # Thin variant: the same finding, but only the public-header AST is present,
    # so a single provider corroborates it.
    return case144_audit_private_header_leak()


#: case dir name → {fixture filename: builder}. ``snapshot.abi.json`` is the
#: primary fixture; extra entries (case151's ``thin``) are secondary variants.
FIXTURES: dict[str, dict[str, object]] = {
    "case143_audit_accidental_export": {
        "snapshot.abi.json": case143_audit_accidental_export
    },
    "case144_audit_private_header_leak": {
        "snapshot.abi.json": case144_audit_private_header_leak
    },
    "case145_audit_unversioned_export": {
        "snapshot.abi.json": case145_audit_unversioned_export
    },
    "case146_audit_rtti_for_internal": {
        "snapshot.abi.json": case146_audit_rtti_for_internal
    },
    "case147_scan_depth_ladder": {"snapshot.abi.json": case147_scan_depth_ladder},
    "case148_xcheck_header_build_mismatch": {
        "snapshot.abi.json": case148_xcheck_header_build_mismatch
    },
    "case149_xcheck_odr_variant": {"snapshot.abi.json": case149_xcheck_odr_variant},
    "case150_xcheck_export_public_pair": {
        "snapshot.abi.json": case150_xcheck_export_public_pair
    },
    "case151_xcheck_provider_matrix": {
        "snapshot.abi.json": case151_xcheck_provider_matrix,
        "thin.abi.json": case151_xcheck_provider_matrix_thin,
    },
}


def _render(builder) -> str:
    snap = builder()  # type: ignore[operator]
    return json.dumps(snapshot_to_dict(snap), indent=2, sort_keys=True) + "\n"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if a committed fixture differs from what would be generated.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    drift = False
    written = 0
    for case_name, files in FIXTURES.items():
        case_dir = EXAMPLES / case_name
        for filename, builder in files.items():
            content = _render(builder)
            path = case_dir / filename
            if args.check:
                current = path.read_text(encoding="utf-8") if path.is_file() else ""
                if current != content:
                    print(f"drift: {path.relative_to(ROOT)}", file=sys.stderr)
                    drift = True
            else:
                case_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                written += 1

    if args.check:
        if drift:
            print(
                "G20 fixtures out of date. Run: python scripts/gen_g20_fixtures.py",
                file=sys.stderr,
            )
            return 1
        print("G20 fixtures up to date.")
        return 0
    print(f"Wrote {written} G20 fixture file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
