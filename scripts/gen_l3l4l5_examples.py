#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
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

"""Single source of truth for the L3/L4/L5 build/source-only example fixtures.

Each case demonstrates an ABI/API failure that *only* build context (L3),
source-replay surfaces (L4), or the derived source graph (L5) can see — no
artifact layer does. Unlike the binary-diff catalog, these cases ship a hand-
built pair of evidence-model fixtures (``old.json`` + ``new.json``) instead of
compiled ``v1``/``v2`` binaries, so the corpus is validated in the fast lane by
``tests/test_l3l4l5_examples.py`` with no compiler / castxml.

* **L3** fixtures are ``BuildEvidence`` dicts; the case runs ``diff_build_evidence``.
* **L4** fixtures are ``SourceAbiSurface`` dicts; the case runs ``diff_source_abi``.
* **L5** fixtures are ``SourceGraphSummary`` dicts; the case runs
  ``diff_source_graph_findings``.

Run ``python scripts/gen_l3l4l5_examples.py`` to (re)write the committed
fixtures; ``--check`` fails if they drift. The per-case ``expected_kinds`` live
in ``examples/ground_truth.json`` (the catalog's single source of truth); this
script only owns the fixture *bytes*.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from abicheck.buildsource.adapters.base import derive_build_options  # noqa: E402
from abicheck.buildsource.build_evidence import (  # noqa: E402
    BuildEvidence,
    CompileUnit,
    Target,
    TargetKind,
)
from abicheck.buildsource.source_abi import (  # noqa: E402
    SourceAbiSurface,
    SourceEntity,
    SourceLocation,
)
from abicheck.buildsource.source_graph import (  # noqa: E402
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
)

EXAMPLES = _REPO / "examples"


# ---------------------------------------------------------------------------
# L3 — build-evidence pairs
# ---------------------------------------------------------------------------
def _build_evidence(flags: list[str]) -> dict[str, Any]:
    cu = CompileUnit(id="tu0", source="src/lib.cpp", language="CXX", abi_relevant_flags=flags)
    ev = BuildEvidence(
        targets=[Target(id="libdemo", name="demo", kind=TargetKind.SHARED_LIBRARY)],
        compile_units=[cu],
        build_options=derive_build_options([cu]),
    )
    return ev.to_dict()


# ---------------------------------------------------------------------------
# L4 — source-abi surface pairs
# ---------------------------------------------------------------------------
def _loc(path: str, line: int) -> SourceLocation:
    return SourceLocation(path=path, line=line)


def _surface(**buckets: list[SourceEntity]) -> dict[str, Any]:
    return SourceAbiSurface(library="libdemo.so", **buckets).to_dict()


# ---------------------------------------------------------------------------
# L5 — source-graph pairs
# ---------------------------------------------------------------------------
def _graph(nodes: list[GraphNode], edges: list[GraphEdge]) -> dict[str, Any]:
    return SourceGraphSummary(nodes=nodes, edges=edges).to_dict()


def _N(nid: str, kind: str, label: str) -> GraphNode:
    return GraphNode(id=nid, kind=kind, label=label)


def _E(src: str, dst: str, kind: str) -> GraphEdge:
    return GraphEdge(src=src, dst=dst, kind=kind)


# ---------------------------------------------------------------------------
# Case construction — {case_name: (layer, old_dict, new_dict)}
# ---------------------------------------------------------------------------
def build_cases() -> dict[str, tuple[str, dict[str, Any], dict[str, Any]]]:
    cases: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}

    # ---- L3 ----------------------------------------------------------------
    cases["case152_enum_size_flag_flip"] = (
        "L3", _build_evidence([]), _build_evidence(["-fshort-enums"]),
    )
    # Struct packing's compiler default is target-dependent (GCC/Clang natural
    # vs MSVC /Zp8), so a flip is only reported when both sides are explicit —
    # here pack width 8 vs 1.
    cases["case153_struct_packing_flip"] = (
        "L3", _build_evidence(["-fpack-struct=8"]), _build_evidence(["-fpack-struct=1"]),
    )
    cases["case154_lto_mode_flip"] = (
        "L3", _build_evidence([]), _build_evidence(["-flto"]),
    )
    cases["case155_char_signedness_flip"] = (
        "L3", _build_evidence(["-fsigned-char"]), _build_evidence(["-funsigned-char"]),
    )

    # ---- L4 ----------------------------------------------------------------
    hdr = "include/demo/config.h"
    cases["case156_public_macro_removed"] = (
        "L4",
        _surface(reachable_macros=[
            SourceEntity(id="DEMO_MAX_ITEMS", kind="macro", qualified_name="DEMO_MAX_ITEMS",
                         value="64", visibility="public_header", source_location=_loc(hdr, 12)),
        ]),
        _surface(),
    )
    cases["case157_inline_function_removed"] = (
        "L4",
        _surface(reachable_inline_bodies=[
            SourceEntity(id="demo::clamp", kind="inline", qualified_name="demo::clamp",
                         body_hash="sha256:clampv1", visibility="public_header",
                         source_location=_loc("include/demo/math.h", 20)),
        ]),
        _surface(),
    )
    cases["case158_public_typedef_removed"] = (
        "L4",
        _surface(reachable_types=[
            SourceEntity(id="demo::handle_t", kind="typedef", qualified_name="demo::handle_t",
                         type_hash="sha256:h1", value="int", visibility="public_header",
                         source_location=_loc("include/demo/handle.h", 8)),
        ]),
        _surface(),
    )

    # ---- L5 ----------------------------------------------------------------
    # case160: a public entry newly calls an internal (non-public) helper.
    l5_nodes = [
        _N("decl:demo::parse", "source_decl", "demo::parse()"),
        _N("decl:detail::validate", "source_decl", "detail::validate()"),
        _N("sym:_ZN4demo5parseEv", "binary_symbol", "demo::parse"),
        _N("hdr:include/demo/api.h", "header", "demo/api.h"),
    ]
    l5_base = [
        _E("decl:demo::parse", "sym:_ZN4demo5parseEv", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr:include/demo/api.h", "decl:demo::parse", "SOURCE_DECLARES"),
        _E("decl:demo::parse", "decl:demo::parse", "DECL_CALLS_DECL"),
    ]
    cases["case160_public_api_internal_dep_added"] = (
        "L5",
        _graph(l5_nodes, l5_base),
        _graph(l5_nodes, l5_base + [
            _E("decl:demo::parse", "decl:detail::validate", "DECL_CALLS_DECL"),
        ]),
    )

    # case161: the library gains an inter-target build/link dependency.
    l5b_nodes = [
        _N("target:libdemo", "target", "libdemo"),
        _N("target:libcrypto", "target", "libcrypto"),
    ]
    cases["case161_target_dependency_added"] = (
        "L5",
        _graph(l5b_nodes, []),
        _graph(l5b_nodes, [_E("target:libdemo", "target:libcrypto", "TARGET_DEPENDS_ON")]),
    )

    # case162: an exported symbol's declaring file moved. Production graphs attach
    # SOURCE_DECLARES from a `header`-kind node (build_source_graph.header_declares),
    # so the fixture mirrors that: the declaration relocates from legacy.h to init.h.
    l5c_nodes = [
        _N("decl:demo::init", "source_decl", "demo::init()"),
        _N("sym:_ZN4demo4initEv", "binary_symbol", "demo::init"),
        _N("hdr:include/demo/legacy.h", "header", "include/demo/legacy.h"),
        _N("hdr:include/demo/init.h", "header", "include/demo/init.h"),
    ]
    l5c_map = _E("decl:demo::init", "sym:_ZN4demo4initEv", "SOURCE_DECL_MAPS_TO_SYMBOL")
    cases["case162_symbol_source_owner_changed"] = (
        "L5",
        _graph(l5c_nodes, [l5c_map, _E("hdr:include/demo/legacy.h", "decl:demo::init", "SOURCE_DECLARES")]),
        _graph(l5c_nodes, [l5c_map, _E("hdr:include/demo/init.h", "decl:demo::init", "SOURCE_DECLARES")]),
    )

    return cases


def _write_or_check(case_name: str, side: str, data: dict[str, Any], *, check: bool) -> bool:
    path = EXAMPLES / case_name / f"{side}.json"
    rendered = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if check:
        if not path.is_file():
            print(f"MISSING: {path.relative_to(_REPO)}")
            return False
        if path.read_text() != rendered:
            print(f"DRIFT: {path.relative_to(_REPO)}")
            return False
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify committed fixtures are in sync")
    args = ap.parse_args()

    ok = True
    for case_name, (_layer, old, new) in build_cases().items():
        ok &= _write_or_check(case_name, "old", old, check=args.check)
        ok &= _write_or_check(case_name, "new", new, check=args.check)

    if args.check:
        if ok:
            print("L3/L4/L5 example fixtures in sync")
            return 0
        print("L3/L4/L5 example fixtures drifted; run scripts/gen_l3l4l5_examples.py")
        return 1
    print(f"wrote fixtures for {len(build_cases())} L3/L4/L5 example cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
