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

"""Generate the committed snapshot fixtures for the ADR-044 P2 item 3 example
cases (192-193): reachability-aware suppression's headline scenario, end to
end, and its deliberate counter-example.

Each case ships a hand-built pair of :class:`~abicheck.model.AbiSnapshot`
objects (with an embedded L5 ``source_graph``) serialized to
``examples/caseNN_*/old.abi.json`` / ``new.abi.json``, plus a
``suppress.yaml`` demonstrating the reachability-aware suppression gate --
the same ``old.abi.json``/``new.abi.json`` snapshot-pair shape case170 uses,
so each case is directly reproducible via ``abicheck compare old.abi.json
new.abi.json [--suppress suppress.yaml]`` with no compiler needed.

Run ``python scripts/gen_reachability_examples.py`` to (re)write the
committed fixtures; ``--check`` fails if they drift.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from abicheck.buildsource.pack import BuildSourcePack  # noqa: E402
from abicheck.buildsource.source_graph import (  # noqa: E402
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
)
from abicheck.model import AbiSnapshot, Function, Visibility  # noqa: E402
from abicheck.serialization import snapshot_to_dict  # noqa: E402

# ---------------------------------------------------------------------------
# case192: a broad internal-namespace suppression rule must not hide a real
# break reached only through a public inline dispatcher's call graph.
# ---------------------------------------------------------------------------

_CASE192_MANGLED = "_ZN4demo6detail13compute_avx2ERKNS_10DescriptorE"


def _case192_graph() -> SourceGraphSummary:
    # demo::compute is `inline` (its body is compiled into every consumer's
    # own translation unit) and its body dispatches to the internal
    # detail::compute_avx2 specialization -- the exact oneDAL-style shape
    # this ADR is named for. consumer_compiled_body=True is the signal
    # (source_graph.py's build_source_graph computes it from whether the L4
    # surface routed the entity through reachable_inline_bodies/
    # reachable_templates) that lets the call-graph walk treat demo::compute
    # as a genuine entry at all -- see case193 for the entry that fails this
    # check on purpose.
    return SourceGraphSummary(
        nodes=[
            GraphNode(
                id="decl://compute",
                kind="source_decl",
                label="demo::compute",
                attrs={
                    "visibility": "public_header",
                    "decl_kind": "inline",
                    "consumer_compiled_body": True,
                },
            ),
            GraphNode(
                id="decl://compute_avx2",
                kind="source_decl",
                label="demo::detail::compute_avx2",
                attrs={"visibility": "source"},
            ),
        ],
        edges=[
            GraphEdge(
                src="decl://compute", dst="decl://compute_avx2", kind="DECL_CALLS_DECL"
            ),
            GraphEdge(
                src="decl://compute_avx2",
                dst=f"binary_symbol://{_CASE192_MANGLED}",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            ),
        ],
    )


def case192_old() -> AbiSnapshot:
    return AbiSnapshot(
        library="libdemo.so.1",
        version="1.0",
        functions=[
            Function(
                name="demo::compute",
                mangled="_ZN4demo7computeERKNS_10DescriptorE",
                return_type="Result",
                is_inline=True,
                visibility=Visibility.PUBLIC,
            ),
            Function(
                name="demo::detail::compute_avx2",
                mangled=_CASE192_MANGLED,
                return_type="Result",
                visibility=Visibility.PUBLIC,
            ),
        ],
        build_source=BuildSourcePack(root="", source_graph=_case192_graph()),
    )


def case192_new() -> AbiSnapshot:
    # detail::compute_avx2 is removed outright (e.g. folded into a different
    # dispatch specialization) -- demo::compute's own signature is untouched,
    # so nothing about compute() itself looks any different to an artifact
    # diff; only the call-graph walk sees that compute()'s own body no longer
    # resolves.
    return AbiSnapshot(
        library="libdemo.so.1",
        version="2.0",
        functions=[
            Function(
                name="demo::compute",
                mangled="_ZN4demo7computeERKNS_10DescriptorE",
                return_type="Result",
                is_inline=True,
                visibility=Visibility.PUBLIC,
            ),
        ],
    )


_CASE192_SUPPRESS_REFUSED = """\
version: 1
suppressions:
  - namespace: "demo::detail::**"
    reason: "detail:: is our internal implementation namespace"
"""

_CASE192_SUPPRESS_ACKNOWLEDGED = """\
version: 1
suppressions:
  - namespace: "demo::detail::**"
    reason: "Reviewed: compute_avx2 removal tracked in MYLIB-1234, consumers migrated"
    allow_public_break: true
"""


# ---------------------------------------------------------------------------
# case193: the deliberate counter-example -- an ordinary, out-of-line exported
# function's internal call is *not* public-reachable, so the same broad
# suppression rule applies cleanly with no diagnostic at all.
# ---------------------------------------------------------------------------

_CASE193_MANGLED = "_ZN4demo6detail11log_contextEv"


def _case193_graph() -> SourceGraphSummary:
    # demo::api is an ordinary, out-of-line exported function -- its body is
    # compiled into libdemo.so only, never into any consumer's own binary, so
    # consumer_compiled_body=False. Its call to detail::log_context happens
    # entirely inside libdemo.so; removing log_context can only break
    # libdemo.so's own build, never a consumer that links only against api()'s
    # exported symbol.
    return SourceGraphSummary(
        nodes=[
            GraphNode(
                id="decl://api",
                kind="source_decl",
                label="demo::api",
                attrs={
                    "visibility": "public_header",
                    "decl_kind": "function",
                    "consumer_compiled_body": False,
                },
            ),
            GraphNode(
                id="decl://log_context",
                kind="source_decl",
                label="demo::detail::log_context",
                attrs={"visibility": "source"},
            ),
        ],
        edges=[
            GraphEdge(
                src="decl://api", dst="decl://log_context", kind="DECL_CALLS_DECL"
            ),
            GraphEdge(
                src="decl://log_context",
                dst=f"binary_symbol://{_CASE193_MANGLED}",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            ),
        ],
    )


def case193_old() -> AbiSnapshot:
    return AbiSnapshot(
        library="libdemo.so.1",
        version="1.0",
        functions=[
            Function(
                name="demo::api",
                mangled="_ZN4demo3apiEv",
                return_type="void",
                visibility=Visibility.PUBLIC,
            ),
            Function(
                name="demo::detail::log_context",
                mangled=_CASE193_MANGLED,
                return_type="void",
                visibility=Visibility.PUBLIC,
            ),
        ],
        build_source=BuildSourcePack(root="", source_graph=_case193_graph()),
    )


def case193_new() -> AbiSnapshot:
    return AbiSnapshot(
        library="libdemo.so.1",
        version="2.0",
        functions=[
            Function(
                name="demo::api",
                mangled="_ZN4demo3apiEv",
                return_type="void",
                visibility=Visibility.PUBLIC,
            ),
        ],
    )


_CASE193_SUPPRESS = """\
version: 1
suppressions:
  - namespace: "demo::detail::**"
    reason: "detail:: is our internal implementation namespace"
"""

#: case dir name -> {fixture filename: builder-or-static-content}. Callables
#: build an AbiSnapshot (rendered to JSON below); plain strings are written
#: verbatim (the suppress.yaml files).
FIXTURES: dict[str, dict[str, object]] = {
    "case192_call_graph_break_survives_suppression": {
        "old.abi.json": case192_old,
        "new.abi.json": case192_new,
        "suppress-refused.yaml": _CASE192_SUPPRESS_REFUSED,
        "suppress-acknowledged.yaml": _CASE192_SUPPRESS_ACKNOWLEDGED,
    },
    "case193_ordinary_exported_fn_call_not_reachable": {
        "old.abi.json": case193_old,
        "new.abi.json": case193_new,
        "suppress.yaml": _CASE193_SUPPRESS,
    },
}


def _render(builder: object) -> str:
    if isinstance(builder, str):
        return builder
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
                "Reachability example fixtures out of date. Run: "
                "python scripts/gen_reachability_examples.py",
                file=sys.stderr,
            )
            return 1
        print("Reachability example fixtures up to date.")
        return 0
    print(f"Wrote {written} reachability example fixture file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
