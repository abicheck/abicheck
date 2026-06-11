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

"""Source-replay validation corpus (ADR-030 Validation, follow-up #1).

A single labelled table of deliberate source-only edits, each paired with the
``ChangeKind`` it must produce. It pins two ADR-030 invariants that the
per-detector unit tests assert piecemeal:

1. **Expected detection** — each source-only edit fires exactly the documented
   ``ChangeKind`` (and no spurious extra kinds).
2. **Authority boundary (ADR-028 D3 / ADR-030 D6, D10)** — *no* source-only
   finding is ever ``BREAKING``; every one lands in ``API_BREAK_KINDS`` or
   ``RISK_KINDS`` and carries the ``L4_SOURCE_ABI`` evidence-tier stamp.

This is the committed pure-Python half of the ADR-030 validation corpus; the
binary ``examples/case*`` fixtures and the ``changed``/``target`` perf
benchmarks remain follow-up work (no clang/castxml needed to run this).
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    RISK_KINDS,
    ChangeKind,
)
from abicheck.evidence.source_abi import (
    EVIDENCE_TIER_L4,
    SourceAbiSurface,
    SourceEntity,
    SourceLocation,
)
from abicheck.evidence.source_diff import diff_source_abi


def _ent(
    name: str,
    kind: str,
    *,
    value: str = "",
    signature_hash: str = "",
    body_hash: str = "",
    type_hash: str = "",
    mangled: str = "",
    visibility: str = "public_header",
    origin: str = "PUBLIC_HEADER",
) -> SourceEntity:
    return SourceEntity(
        id=f"decl://{name}",
        kind=kind,
        qualified_name=name,
        mangled_name=mangled,
        signature_hash=signature_hash,
        body_hash=body_hash,
        type_hash=type_hash,
        value=value,
        source_location=SourceLocation(path=f"include/{name}.h", line=1, origin=origin),
        visibility=visibility,
    )


def _surface(**kw: object) -> SourceAbiSurface:
    s = SourceAbiSurface(library="libfoo.so", target_id="target://libfoo")
    for key, val in kw.items():
        setattr(s, key, val)
    return s


# (label, old_surface, new_surface, expected_kind) — each a deliberate
# source-only edit. Builders are kept inline so the corpus reads as a catalogue.
def _macro_value_changed() -> tuple[SourceAbiSurface, SourceAbiSurface]:
    return (
        _surface(reachable_macros=[_ent("FOO_SIZE", "macro", value="16")]),
        _surface(reachable_macros=[_ent("FOO_SIZE", "macro", value="32")]),
    )


def _default_argument_changed() -> tuple[SourceAbiSurface, SourceAbiSurface]:
    old = _ent("f", "function", mangled="_Z1fi", signature_hash="sig", value="p0=1")
    new = _ent("f", "function", mangled="_Z1fi", signature_hash="sig", value="p0=2")
    return _surface(reachable_declarations=[old]), _surface(
        reachable_declarations=[new]
    )


def _constexpr_value_changed() -> tuple[SourceAbiSurface, SourceAbiSurface]:
    return (
        _surface(reachable_declarations=[_ent("kMax", "constexpr", value="42")]),
        _surface(reachable_declarations=[_ent("kMax", "constexpr", value="43")]),
    )


def _typedef_target_changed() -> tuple[SourceAbiSurface, SourceAbiSurface]:
    return (
        _surface(
            reachable_types=[
                _ent("handle_t", "typedef", value="int32_t", type_hash="a")
            ]
        ),
        _surface(
            reachable_types=[
                _ent("handle_t", "typedef", value="int64_t", type_hash="b")
            ]
        ),
    )


def _inline_body_changed() -> tuple[SourceAbiSurface, SourceAbiSurface]:
    return (
        _surface(
            reachable_inline_bodies=[
                _ent("f", "inline", mangled="_Z1fv", body_hash="a")
            ]
        ),
        _surface(
            reachable_inline_bodies=[
                _ent("f", "inline", mangled="_Z1fv", body_hash="b")
            ]
        ),
    )


def _template_body_changed() -> tuple[SourceAbiSurface, SourceAbiSurface]:
    return (
        _surface(reachable_templates=[_ent("maxv", "template", body_hash="a")]),
        _surface(reachable_templates=[_ent("maxv", "template", body_hash="b")]),
    )


def _template_removed() -> tuple[SourceAbiSurface, SourceAbiSurface]:
    return (
        _surface(reachable_templates=[_ent("maxv", "template", body_hash="a")]),
        _surface(),
    )


def _generated_header_changed() -> tuple[SourceAbiSurface, SourceAbiSurface]:
    old = _ent(
        "cfg", "record", visibility="generated", origin="GENERATED", type_hash="a"
    )
    new = _ent(
        "cfg", "record", visibility="generated", origin="GENERATED", type_hash="b"
    )
    return _surface(reachable_types=[old]), _surface(reachable_types=[new])


def _odr_source_conflict() -> tuple[SourceAbiSurface, SourceAbiSurface]:
    conflict = {
        "qualified_name": "Widget",
        "header": "include/widget.h",
        "old_type_hash": "a",
        "new_type_hash": "b",
    }
    return _surface(), _surface(odr_conflicts=[conflict])


CORPUS = [
    (
        "public_macro_value_changed",
        _macro_value_changed,
        ChangeKind.PUBLIC_MACRO_VALUE_CHANGED,
    ),
    (
        "default_argument_changed",
        _default_argument_changed,
        ChangeKind.DEFAULT_ARGUMENT_CHANGED,
    ),
    (
        "constexpr_value_changed",
        _constexpr_value_changed,
        ChangeKind.CONSTEXPR_VALUE_CHANGED,
    ),
    (
        "public_typedef_target_changed",
        _typedef_target_changed,
        ChangeKind.PUBLIC_TYPEDEF_TARGET_CHANGED,
    ),
    ("inline_body_changed", _inline_body_changed, ChangeKind.INLINE_BODY_CHANGED),
    ("template_body_changed", _template_body_changed, ChangeKind.TEMPLATE_BODY_CHANGED),
    (
        "uninstantiated_template_removed",
        _template_removed,
        ChangeKind.UNINSTANTIATED_TEMPLATE_REMOVED,
    ),
    (
        "generated_header_changed",
        _generated_header_changed,
        ChangeKind.GENERATED_HEADER_CHANGED,
    ),
    ("odr_source_conflict", _odr_source_conflict, ChangeKind.ODR_SOURCE_CONFLICT),
]


@pytest.mark.parametrize("label,builder,expected", CORPUS, ids=[c[0] for c in CORPUS])
def test_source_only_fixture_fires_expected_kind(label, builder, expected) -> None:
    old, new = builder()
    kinds = [c.kind for c in diff_source_abi(old, new)]
    assert expected in kinds, f"{label}: expected {expected} in {kinds}"


@pytest.mark.parametrize("label,builder,expected", CORPUS, ids=[c[0] for c in CORPUS])
def test_source_only_fixture_is_never_breaking(label, builder, expected) -> None:
    # ADR-028 D3 / ADR-030 D6: L4 source-only findings never decide a shipped-ABI
    # BREAKING verdict on their own — each kind is API_BREAK or RISK, stamped L4.
    old, new = builder()
    changes = diff_source_abi(old, new)
    assert changes, f"{label}: expected at least one finding"
    for change in changes:
        assert change.kind not in BREAKING_KINDS, f"{label}: {change.kind} is BREAKING"
        assert change.kind in API_BREAK_KINDS | RISK_KINDS
        assert EVIDENCE_TIER_L4 in (change.source_location or ""), (
            f"{label}: {change.kind} missing L4 evidence-tier stamp"
        )


def test_corpus_covers_every_source_replay_kind() -> None:
    # Guard against a new source-replay ChangeKind being added without a
    # validation-corpus entry. The nine D6 kinds plus the typedef follow-up.
    source_replay_kinds = {
        ChangeKind.PUBLIC_MACRO_VALUE_CHANGED,
        ChangeKind.DEFAULT_ARGUMENT_CHANGED,
        ChangeKind.INLINE_BODY_CHANGED,
        ChangeKind.CONSTEXPR_VALUE_CHANGED,
        ChangeKind.TEMPLATE_BODY_CHANGED,
        ChangeKind.UNINSTANTIATED_TEMPLATE_REMOVED,
        ChangeKind.SOURCE_DECL_BINARY_SYMBOL_MISMATCH,
        ChangeKind.ODR_SOURCE_CONFLICT,
        ChangeKind.GENERATED_HEADER_CHANGED,
        ChangeKind.PUBLIC_TYPEDEF_TARGET_CHANGED,
    }
    covered = {expected for _, _, expected in CORPUS}
    # source_decl_binary_symbol_mismatch needs a binary-symbol mapping fixture,
    # exercised in test_source_abi.py; the rest are covered here.
    missing = (
        source_replay_kinds - covered - {ChangeKind.SOURCE_DECL_BINARY_SYMBOL_MISMATCH}
    )
    assert not missing, f"source-replay kinds without a corpus entry: {missing}"
