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

"""``--contract public`` closes its domain only on raw-header-text evidence.

Bug class (design-hardening F1, read both ways): "the active parse did not
see a declaration" was about to be treated as "the headers never declare
it", which hides catalog case97 (a declaration inside an inactive ``#if``)
-- and, the other way, a decision reported "required evidence incomplete"
for an export the complete header domain provably never mentions (zstd's
internal ``ZSTD_decodeLiteralsBlock``).

Oracles are restated independently of the implementation: header text is
built from known identifier sets, mangled names are generated from known
leaves, and the closure rule is re-derived from the four facts it rests on.
"""

from __future__ import annotations

import itertools
import string
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.contract_evaluation import evaluate_change_contract_relevance
from abicheck.contract_evidence_collect import public_header_evidence
from abicheck.contract_relevance_types import ContractMode, ContractRelevance
from abicheck.elf_metadata import ElfMetadata
from abicheck.extract.public_header_identifiers import (
    identifiers_in_header_text,
    scan_public_header_identifiers,
)
from abicheck.model import AbiSnapshot, Function
from abicheck.model.elf_facts import ElfSymbol, SymbolType
from abicheck.model.fact import Fact, FactStatus
from abicheck.model.symbol_leaf import symbol_leaf_identifier
from abicheck.model.vocabulary import ScopeOrigin
from abicheck.policy.public_surface_closure import resolve_public_surface
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

# ── scanner ──────────────────────────────────────────────────────────────────


def test_inactive_preprocessor_branches_are_scanned():
    # Catalog case97's shape: the declaration is only in an #ifdef branch.
    text = "namespace lib {\n#ifdef USE_FEATURE\nvoid extended();\n#endif\nvoid basic();\n}\n"
    found = identifiers_in_header_text(text)
    assert found is not None and {"extended", "basic", "USE_FEATURE"} <= found


def test_comments_and_literals_do_not_count():
    text = '/* old_api() was removed */\n// see legacy_fn\nconst char* s = "quoted_name";\nint real_fn(void);\n'
    found = identifiers_in_header_text(text)
    assert found is not None
    assert "real_fn" in found
    assert not {"old_api", "legacy_fn", "quoted_name"} & found


def test_token_paste_makes_the_index_unknown():
    assert identifiers_in_header_text("#define DECL(n) void lib_##n(void);\n") is None
    # A '##' inside a comment is prose, not the operator.
    assert identifiers_in_header_text("/* ## heading */ int f(void);\n") is not None


@given(
    st.sets(
        st.text(alphabet=string.ascii_letters + "_", min_size=1, max_size=8).map(
            lambda s: "x" + s
        ),
        max_size=12,
    )
)
def test_every_declared_identifier_is_found(names):
    text = "".join(f"int {n}(void);\n" for n in sorted(names))
    found = identifiers_in_header_text(text)
    assert found is not None and set(names) <= found


def test_directories_expand_and_missing_entries_are_not_vouched_for(tmp_path: Path):
    inc = tmp_path / "include" / "sub"
    inc.mkdir(parents=True)
    (inc / "a.h").write_text("int alpha(void);\n")
    (tmp_path / "include" / "b.hpp").write_text("int beta();\n")
    (tmp_path / "include" / "notes.txt").write_text("int gamma();\n")
    (tmp_path / "paste.h").write_text("#define D(n) int x_##n;\n")
    found = scan_public_header_identifiers([], [tmp_path / "include"])
    assert found.status is FactStatus.PRESENT
    assert {"alpha", "beta"} <= found.value and "gamma" not in found.value
    # Every non-present outcome says why, and none of them is "empty".
    missing = scan_public_header_identifiers([tmp_path / "missing.h"])
    assert missing.status is FactStatus.FAILED and missing.diagnostics
    pasted = scan_public_header_identifiers([tmp_path / "paste.h"])
    assert pasted.status is FactStatus.UNSUPPORTED and pasted.diagnostics
    none = scan_public_header_identifiers(None, None)
    assert none.status is FactStatus.NOT_COLLECTED and none.diagnostics


# ── leaf parser ──────────────────────────────────────────────────────────────

_IDENT = st.text(alphabet=string.ascii_letters + "_", min_size=1, max_size=10).map(
    lambda s: "n" + s
)


@given(st.lists(_IDENT, min_size=0, max_size=3), _IDENT)
def test_leaf_of_a_generated_mangled_name_is_recovered(scopes, leaf):
    # Oracle: build the Itanium name from known parts.
    if scopes:
        mangled = "_ZN" + "".join(f"{len(s)}{s}" for s in [*scopes, leaf]) + "Ev"
    else:
        mangled = f"_Z{len(leaf)}{leaf}v"
    assert symbol_leaf_identifier(mangled) == leaf
    assert symbol_leaf_identifier(mangled + "@@VERS_1") == leaf


@pytest.mark.parametrize(
    "symbol",
    ["_ZN3FooIiE3barEv", "_ZSt4cout", "_ZplRK1AS1_", "_ZZ3foovE1x", "", "@@V1", "a.b"],
)
def test_unsupported_shapes_answer_none(symbol):
    assert symbol_leaf_identifier(symbol) is None


def test_c_names_and_constructors():
    assert (
        symbol_leaf_identifier("ZSTD_decodeLiteralsBlock") == "ZSTD_decodeLiteralsBlock"
    )
    assert symbol_leaf_identifier("_ZN3FooC1Ev") == "Foo"


# ── storage ──────────────────────────────────────────────────────────────────


def test_round_trip_and_unknown_stays_unknown():
    snap = AbiSnapshot(
        library="l",
        version="1",
        public_header_identifiers_fact=Fact.present(frozenset({"b", "a"})),
    )
    d = snapshot_to_dict(snap)
    assert "public_header_identifiers" not in d  # only the Fact is persisted
    back = snapshot_from_dict(d)
    assert back.public_header_identifiers == frozenset({"a", "b"})
    assert back.public_header_identifiers_fact.status is FactStatus.PRESENT
    for fact in (
        Fact.unsupported("token paste (##) in x.h"),
        Fact.failed("unreadable: y.h"),
    ):
        rt = snapshot_from_dict(
            snapshot_to_dict(
                AbiSnapshot(
                    library="l", version="1", public_header_identifiers_fact=fact
                )
            )
        )
        assert rt.public_header_identifiers is None
        assert rt.public_header_identifiers_fact.status is fact.status
        assert rt.public_header_identifiers_fact.diagnostics == fact.diagnostics
    bare = snapshot_to_dict(AbiSnapshot(library="l", version="1"))
    assert "public_header_identifiers_fact" not in bare  # encodes as v53 did
    loaded = snapshot_from_dict(bare)
    assert loaded.public_header_identifiers is None
    assert loaded.public_header_identifiers_fact.status is FactStatus.NOT_COLLECTED


def test_cache_key_follows_the_snapshot_schema_version(tmp_path: Path, monkeypatch):
    from abicheck import snapshot_cache

    so = tmp_path / "lib.so"
    so.write_bytes(b"\x7fELF")
    first = snapshot_cache._cache_key(so, [], [], "1", "c")
    monkeypatch.setattr(snapshot_cache, "_snapshot_schema_version", lambda: 10_000)
    assert snapshot_cache._cache_key(so, [], [], "1", "c") != first


# ── the closure rule ─────────────────────────────────────────────────────────


def _snap(
    *, declared: list[str], exports: list[str], spelled: frozenset[str] | None
) -> AbiSnapshot:
    snap = AbiSnapshot(
        library="libx.so",
        version="1",
        functions=[
            Function(
                name=n,
                mangled=n,
                return_type="int",
                params=[],
                origin=ScopeOrigin.PUBLIC_HEADER,
                source_header="/inc/api.h",
            )
            for n in declared
        ],
        elf=ElfMetadata(
            symbols=[
                ElfSymbol(
                    name=n,
                    binding="GLOBAL",
                    sym_type=SymbolType.FUNC,
                    size=8,
                    is_default=True,
                )
                for n in exports
            ]
        ),
        from_headers=True,
    )
    if spelled is not None:
        snap.public_header_identifiers_fact = Fact.present(spelled)
        snap.public_header_identifiers = spelled
    return snap


_SPELLINGS = {
    "absent": frozenset({"api", "int"}),
    "spelled_in_inactive_branch": frozenset({"api", "helper", "USE_FEATURE"}),
    "not_captured": None,
}


def _cases():
    for kind, spelling in itertools.product(
        [ChangeKind.FUNC_REMOVED_ELF_ONLY, ChangeKind.FUNC_ADDED_ELF_ONLY],
        _SPELLINGS,
    ):
        having = _snap(
            declared=["api"], exports=["api", "helper"], spelled=_SPELLINGS[spelling]
        )
        lacking = _snap(declared=["api"], exports=["api"], spelled=_SPELLINGS[spelling])
        removal = kind is ChangeKind.FUNC_REMOVED_ELF_ONLY
        yield kind, spelling, *((having, lacking) if removal else (lacking, having))


@pytest.mark.parametrize(("kind", "spelling", "old", "new"), list(_cases()))
def test_closed_only_when_complete_captured_and_unspelled(kind, spelling, old, new):
    decision = evaluate_change_contract_relevance(
        Change(kind=kind, symbol="helper", description="x"),
        resolve_public_surface(old),
        resolve_public_surface(new),
        mode=ContractMode.PUBLIC,
    )
    judged, side = (
        (old, "old") if kind is ChangeKind.FUNC_REMOVED_ELF_ONLY else (new, "new")
    )
    record = public_header_evidence(judged, resolve_public_surface(judged), side).record
    expected = (
        record.completeness.value == "complete"
        and judged.public_header_identifiers_fact.status is FactStatus.PRESENT
        and "helper" not in judged.public_header_identifiers
    )
    assert (decision.relevance is ContractRelevance.UNKNOWN_UNPROVEN) is expected, (
        decision
    )
    if expected:
        assert decision.reason_code == "closed_domain_no_commitment"


def test_the_rule_is_not_vacuous():
    outcomes = set()
    for kind, spelling, old, new in _cases():
        d = evaluate_change_contract_relevance(
            Change(kind=kind, symbol="helper", description="x"),
            resolve_public_surface(old),
            resolve_public_surface(new),
            mode=ContractMode.PUBLIC,
        )
        outcomes.add(d.relevance is ContractRelevance.UNKNOWN_UNPROVEN)
    assert outcomes == {True, False}


def test_unknown_unproven_has_exactly_one_construction_site():
    import ast
    import inspect

    import abicheck.contract_evaluation as evaluation
    import abicheck.policy.contract_closed_domain as closed

    def sites(mod):
        tree = ast.parse(inspect.getsource(mod))
        return sorted(
            {
                fn.name
                for fn in ast.walk(tree)
                if isinstance(fn, ast.FunctionDef)
                for node in ast.walk(fn)
                if isinstance(node, ast.Attribute) and node.attr == "UNKNOWN_UNPROVEN"
            }
        )

    assert sites(evaluation) == []
    assert sites(closed) == ["closed_domain_decision"]


def test_stamping_sets_the_fact_and_the_bridged_field(tmp_path: Path):
    from abicheck.extract.public_header_identifiers import (
        stamp_public_header_identifiers,
    )

    (tmp_path / "a.h").write_text("int alpha(void);\n")
    snap = AbiSnapshot(library="l", version="1")
    stamp_public_header_identifiers(snap, [tmp_path / "a.h"], None)
    assert snap.public_header_identifiers_fact.status is FactStatus.PRESENT
    assert snap.public_header_identifiers is not None
    assert "alpha" in snap.public_header_identifiers
    stamp_public_header_identifiers(snap, [tmp_path / "gone.h"], None)
    assert snap.public_header_identifiers_fact.status is FactStatus.FAILED
    assert snap.public_header_identifiers is None


@pytest.mark.parametrize(
    "number",
    ["1'000", "0xFF'FF", "0b1010'0101", "1'000'000ull", "3.141'59", "0'7"],
)
@pytest.mark.parametrize("literal", ["'x'", "L'x'", "u8'x'", "u'x'", "U'x'", "'\\''"])
def test_digit_separator_never_swallows_following_identifiers(
    number: str, literal: str
) -> None:
    """A digit separator is not a character literal's opening quote: names
    after it survive, and a later character literal is still stripped."""
    text = f"int a[{number}]; void helper(char c = {literal}); int tail;"
    found = identifiers_in_header_text(text)
    assert found is not None
    assert {"helper", "char", "c", "tail"} <= found
    assert "x" not in found


@pytest.mark.parametrize("digit", ["²", "٣", "۴", "１"])
def test_symbol_leaf_rejects_non_ascii_digits(digit: str) -> None:
    """Non-ASCII digits are not Itanium lengths: unresolved, never a crash."""
    from abicheck.model.symbol_leaf import symbol_leaf_identifier

    assert symbol_leaf_identifier(f"_Z{digit}foov") is None
    assert symbol_leaf_identifier(f"_ZN3ns{digit}fooEv") is None


@pytest.mark.parametrize("mode", ["public", "exports", "all"])
@pytest.mark.parametrize("direction", ["grew", "shrank"])
def test_surface_metric_findings_are_not_applicable(mode: str, direction: str) -> None:
    """A whole-surface count names no contract entity, so it is never
    UNKNOWN_UNRESOLVED -- which would floor the exit to 1 on every run whose
    surface merely changed size."""
    from abicheck.checker import compare
    from abicheck.contract_relevance_types import ContractRelevance
    from abicheck.model import AbiSnapshot, Function, ScopeOrigin, Visibility
    from abicheck.policy.contract_closed_domain import SURFACE_METRIC_KIND_SLUGS

    def fn(n: str) -> Function:
        return Function(
            name=n,
            mangled=n,
            return_type="void",
            visibility=Visibility.PUBLIC,
            origin=ScopeOrigin.PUBLIC_HEADER,
        )

    small = AbiSnapshot(
        library="l.so", version="1", from_headers=True, functions=[fn("a")]
    )
    big = AbiSnapshot(
        library="l.so", version="2", from_headers=True, functions=[fn("a"), fn("b")]
    )
    old, new = (small, big) if direction == "grew" else (big, small)
    r = compare(
        old, new, contract_evaluation=True, contract_mode=mode, surface_metrics=True
    )
    metric = [c for c in r.changes if c.kind.value in SURFACE_METRIC_KIND_SLUGS]
    assert metric, "fixture must produce a surface-metric finding"
    assert all(c.contract_relevance is ContractRelevance.NOT_APPLICABLE for c in metric)
