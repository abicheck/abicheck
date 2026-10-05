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

"""The two findings about one undeclared export state one answer, once.

Bug class (oneCCL/oneDNN validation): ``func_added_elf_only`` and
``exported_not_public`` each decided "what do the public headers say about
this export?" from different evidence, so they disagreed about the same
symbol (an instantiation of a ``template <...>`` the headers declare read as
"not declared in any public header" in one and as public in the other), and
every export that appeared or disappeared was reported twice.

The oracle here is the *construction* of each export shape -- a public
template instantiation, an ``#ifdef``-gated declaration, an internal-namespace
helper, a plain undeclared export, a vtable -- never the shared
``account_export`` decision under test. Every seed draws each shape's
presence on OLD/NEW independently, so additions, removals and persistent
exports of every shape occur together in one comparison.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import pytest

from abicheck.buildsource import export_accounting as acct
from abicheck.buildsource.export_account_decision import ExportAccount
from abicheck.buildsource.export_declaration_evidence import (
    DeclarationHint,
)
from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType
from abicheck.model import AbiSnapshot, Function, ScopeOrigin
from abicheck.policy.evidence_status import CrossSourceEvolution
from abicheck.policy.export_existence_fold import CAUSED_BY_PREFIX, fold_partner
from abicheck.workflows.export_existence_wording import (
    existence_description,
)

_INIT = "_ZN3ccl2v14initEv"


@dataclass(frozen=True)
class _Shape:
    symbol: str
    sym_type: SymbolType
    #: Does the export genuinely lack any public-header declaration?
    undeclared: bool
    #: Text the existence finding must carry for this shape.
    must_say: str


_SHAPES = (
    _Shape(
        "_ZN3ccl2v120create_communicatorsIiiEEiT_T0_",
        SymbolType.FUNC,
        undeclared=False,
        must_say="public template",
    ),
    _Shape(
        "_ZN3ccl2v15gatedEi",
        SymbolType.FUNC,
        undeclared=False,
        must_say="CCL_FEATURE",
    ),
    _Shape(
        "_ZN3ccl6detail6helperEv",
        SymbolType.FUNC,
        undeclared=True,
        must_say="internal namespace",
    ),
    _Shape(
        "_ZN3ccl2v15leakyEv",
        SymbolType.FUNC,
        undeclared=True,
        must_say="not declared in any public header",
    ),
    _Shape(
        "_ZTVN3ccl2v14ImplE",
        SymbolType.OBJECT,
        undeclared=False,
        must_say="C++ ABI artifact",
    ),
)

_HEADER = """#ifndef CCL_HPP
#define CCL_HPP
namespace ccl { inline namespace v1 {
void init();
template <class Device, class Context>
int create_communicators(Device d, Context c);
#ifdef CCL_FEATURE
void gated(int x);
#endif
class Impl { public: virtual ~Impl(); };
} }
#endif
"""


def _side(header: Path, exports: tuple[_Shape, ...], version: str) -> AbiSnapshot:
    init = Function(
        name="init",
        mangled=_INIT,
        return_type="void",
        origin=ScopeOrigin.PUBLIC_HEADER,
        source_header=str(header),
    )
    snap = AbiSnapshot(
        library="libccl.so", version=version, from_headers=True, functions=[init]
    )
    snap.elf = ElfMetadata(
        machine="EM_X86_64",
        symbols=[
            ElfSymbol(name=_INIT, binding="GLOBAL", sym_type=SymbolType.FUNC),
            *(
                ElfSymbol(name=s.symbol, binding="GLOBAL", sym_type=s.sym_type)
                for s in exports
            ),
        ],
    )
    return snap


_EXISTENCE = {
    ChangeKind.FUNC_ADDED_ELF_ONLY: "added",
    ChangeKind.VAR_ADDED_ELF_ONLY: "added",
    ChangeKind.FUNC_REMOVED_ELF_ONLY: "removed",
    ChangeKind.VAR_REMOVED_ELF_ONLY: "removed",
}


def _run(tmp_path: Path, seed: int):
    rng = random.Random(seed)
    header = tmp_path / "include" / "ccl.hpp"
    header.parent.mkdir(parents=True, exist_ok=True)
    header.write_text(_HEADER)
    presence = {s.symbol: rng.choice(("old", "new", "both", "none")) for s in _SHAPES}
    old = _side(
        header, tuple(s for s in _SHAPES if presence[s.symbol] in ("old", "both")), "1"
    )
    new = _side(
        header, tuple(s for s in _SHAPES if presence[s.symbol] in ("new", "both")), "2"
    )
    return presence, compare(old, new)


@pytest.mark.parametrize("seed", range(40))
def test_existence_and_hygiene_findings_agree_and_never_double_report(
    tmp_path: Path, seed: int
) -> None:
    presence, result = _run(tmp_path, seed)
    shapes = {s.symbol: s for s in _SHAPES}
    kept = list(result.changes)
    redundant = list(result.redundant_changes)
    existence = {c.symbol: c for c in kept if c.kind in _EXISTENCE}
    hygiene_kept = [c for c in kept if c.kind == ChangeKind.EXPORTED_NOT_PUBLIC]
    hygiene_folded = [c for c in redundant if c.kind == ChangeKind.EXPORTED_NOT_PUBLIC]

    for sym, change in existence.items():
        shape = shapes[sym]
        direction = _EXISTENCE[change.kind]
        assert presence[sym] == ("new" if direction == "added" else "old")
        # The wording matches the shape the export was built as.
        assert shape.must_say in change.description, change.description
        claims_absent = "not declared in any public header" in change.description
        assert claims_absent == shape.undeclared, change.description
        if direction == "removed" and shape.undeclared:
            assert "dlsym" in change.description

    # One finding per export event: a hygiene finding for an export that
    # appeared or disappeared is folded into the existence finding, never
    # kept beside it.
    for c in hygiene_kept:
        partner = existence.get(c.symbol)
        assert partner is None or (
            c.cross_source_evolution == CrossSourceEvolution.PERSISTENT
        ), (c.symbol, c.cross_source_evolution)
    for c in hygiene_folded:
        assert (c.caused_by_type or "").startswith(CAUSED_BY_PREFIX)
        partner = existence[c.symbol]
        assert partner.caused_count >= 1
        # And the hygiene finding agrees with its partner about absence.
        assert shapes[c.symbol].undeclared or "CCL_FEATURE" in c.description

    # The hygiene check accounts the same shapes as the existence wording:
    # an undocumented export that appeared/disappeared has a hygiene twin
    # (kept or folded); a documented one (public template, vtable) has none.
    hygiene_syms = {c.symbol for c in (*hygiene_kept, *hygiene_folded)}
    for sym, shape in shapes.items():
        if presence[sym] == "none":
            assert sym not in hygiene_syms
        elif shape.undeclared or "CCL_FEATURE" in shape.must_say:
            assert sym in hygiene_syms, sym
        else:
            assert sym not in hygiene_syms, sym


def test_sweep_is_not_vacuous(tmp_path: Path) -> None:
    """Across the seeds above every shape appears, disappears and folds."""
    seen: set[tuple[str, str]] = set()
    folded = 0
    for seed in range(40):
        sub = tmp_path / str(seed)
        sub.mkdir()
        _presence, result = _run(sub, seed)
        seen |= {
            (c.symbol, _EXISTENCE[c.kind])
            for c in result.changes
            if c.kind in _EXISTENCE
        }
        folded += sum(
            1
            for c in result.redundant_changes
            if (c.caused_by_type or "").startswith(CAUSED_BY_PREFIX)
        )
    for shape in _SHAPES:
        assert (shape.symbol, "added") in seen, shape.symbol
        assert (shape.symbol, "removed") in seen, shape.symbol
    assert folded > 0


@pytest.mark.parametrize("seed", range(12))
def test_folding_changes_no_verdict(tmp_path: Path, seed: int, monkeypatch) -> None:
    """The fold is a presentation decision: the verdict is the one the same
    comparison reaches with the step disabled."""
    _presence, folded = _run(tmp_path / "a", seed)
    from abicheck.policy import export_existence_fold

    monkeypatch.setattr(
        export_existence_fold.FoldExportHygieneIntoExistence,
        "run",
        lambda self, changes, ctx: changes,
    )
    _presence, unfolded = _run(tmp_path / "b", seed)
    assert folded.verdict == unfolded.verdict
    assert len(folded.changes) + len(folded.redundant_changes) == len(
        unfolded.changes
    ) + len(unfolded.redundant_changes)


# --------------------------------------------------------------------------- #
# Unit tables: the wording per accounting category, and the fold's direction
# --------------------------------------------------------------------------- #

#: category -> (claims "not declared in any public header"?, must-have phrase).
#: Stated from what each category *means*, not from the wording function.
_CATEGORY_ORACLE = {
    acct.ACCOUNT_PUBLIC: (False, "public declaration"),
    acct.ACCOUNT_CXX_ARTIFACT: (False, "C++ ABI artifact"),
    acct.ACCOUNT_ALLOCATOR_INTERPOSER: (False, "allocator replacement"),
    acct.ACCOUNT_PUBLIC_TEMPLATE: (False, "public template ns::Box"),
    acct.ACCOUNT_EXTERNAL_DEP: (False, "external dependency libstdc++.so.6"),
    acct.ACCOUNT_INTERNAL_NS: (True, "internal namespace"),
    acct.ACCOUNT_TEMPLATE_INST: (True, "template instantiation"),
    acct.ACCOUNT_OWN_TYPE_INSTANTIATION: (True, "own instantiation"),
    acct.ACCOUNT_UNDECLARED: (True, ""),
}

_KINDS = (
    ChangeKind.FUNC_ADDED_ELF_ONLY,
    ChangeKind.VAR_ADDED_ELF_ONLY,
    ChangeKind.FUNC_REMOVED_ELF_ONLY,
    ChangeKind.VAR_REMOVED_ELF_ONLY,
)


def test_category_oracle_covers_every_accounting_bucket() -> None:
    buckets = {v for k, v in vars(acct).items() if k.startswith("ACCOUNT_")}
    assert buckets == set(_CATEGORY_ORACLE)


@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("category", sorted(_CATEGORY_ORACLE))
@pytest.mark.parametrize("hinted", [False, True])
def test_existence_wording_per_category(kind, category: str, hinted: bool) -> None:
    hint = DeclarationHint("conditional", "/inc/x.hpp", "FEATURE") if hinted else None
    decision = ExportAccount(
        category,
        origin_lib="libstdc++.so.6",
        public_template="ns::Box",
        hint=hint,
    )
    text = existence_description(Change(kind, "_Z1fv", "old wording"), decision) or ""
    claims_absent, phrase = _CATEGORY_ORACLE[category]
    removal = kind in (
        ChangeKind.FUNC_REMOVED_ELF_ONLY,
        ChangeKind.VAR_REMOVED_ELF_ONLY,
    )
    if hinted and category != acct.ACCOUNT_PUBLIC_TEMPLATE:
        # The header text speaks for it: never claim absence.
        assert "FEATURE" in text and "not declared in any" not in text
        return
    assert ("not declared in any public header" in text) == claims_absent, text
    assert phrase in text, text
    # A removal of an export no header promised says whom it can break.
    undocumented = claims_absent or category == acct.ACCOUNT_EXTERNAL_DEP
    assert ("dlsym" in text) == (removal and undocumented), text
    assert text.endswith("_Z1fv")


@pytest.mark.parametrize("state", [*CrossSourceEvolution, None])
@pytest.mark.parametrize("have", [(), ("added",), ("removed",), ("added", "removed")])
def test_fold_direction_matrix(state, have: tuple[str, ...]) -> None:
    hygiene = Change(ChangeKind.EXPORTED_NOT_PUBLIC, "_Z1fv", "hygiene")
    hygiene.cross_source_evolution = state
    existence = {
        ("_Z1fv", d): Change(
            ChangeKind.FUNC_ADDED_ELF_ONLY
            if d == "added"
            else ChangeKind.FUNC_REMOVED_ELF_ONLY,
            "_Z1fv",
            d,
        )
        for d in have
    }
    allowed = {
        CrossSourceEvolution.INTRODUCED: {"added"},
        CrossSourceEvolution.RESOLVED: {"removed"},
        CrossSourceEvolution.NOT_EVALUATED: {"added", "removed"},
    }.get(state, set())
    partner = fold_partner(hygiene, existence)
    if allowed & set(have):
        assert partner is not None and partner.description in allowed
    else:
        assert partner is None


def test_a_resolved_finding_never_scores_even_when_redundant() -> None:
    from abicheck.checker import _verdict_scored_population

    resolved = Change(ChangeKind.EXPORTED_NOT_PUBLIC, "_Z1fv", "gone")
    resolved.cross_source_evolution = CrossSourceEvolution.RESOLVED
    live = Change(ChangeKind.EXPORTED_NOT_PUBLIC, "_Z1gv", "new")
    live.cross_source_evolution = CrossSourceEvolution.INTRODUCED
    for kept, redundant in (
        ([resolved, live], []),
        ([live], [resolved]),
        ([], [resolved, live]),
    ):
        assert _verdict_scored_population(kept, redundant) == [live]
