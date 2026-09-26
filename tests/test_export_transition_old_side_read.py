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

"""Evidence-entity-model gap A2: the OLD-side suppression guard.

``export_transition.surface_exit_is_evidence_gap`` suppresses a surface exit
when OLD "was not exported". That must be *established*: an OLD export table
that was not read answers unknown for every symbol, and suppressing on it
hides a real export loss (false negative). The oracle is a hand-written table
over OLD's table state and OLD's own fact, independent of ``decide``.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import compare
from abicheck.compare.edge_query import observed_export_table
from abicheck.compare.export_transition import surface_exit_is_evidence_gap
from abicheck.model import AbiSnapshot, Fact, Function
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol

# OLD's table: None = no binary; "unread" = parse-failed block; "read_with" /
# "read_without" = parsed table holding / lacking the symbol.
TABLES = ("no_binary", "unread", "read_without", "read_with")
# OLD's own (c) as a producer recorded it for that table.
OLD_FACT = {
    "no_binary": Fact.not_collected("no binary in this dump"),
    "unread": Fact.failed("export table not read"),
    "read_without": Fact.present(False),
    "read_with": Fact.present(True),
}
# Suppress (evidence gap) iff OLD had nothing to lose, or its table was read
# and proves the symbol absent. Written out, not derived.
EXPECT_GAP = {
    "no_binary": True,
    "unread": False,
    "read_without": True,
    "read_with": False,
}


def _elf(table: str) -> ElfMetadata | None:
    if table == "no_binary":
        return None
    names = {"read_with": ["foo", "bar"], "read_without": ["bar"]}.get(table, [])
    return ElfMetadata(
        symbols=[ElfSymbol(name=n) for n in names],
        machine="" if table == "unread" else "EM_X86_64",
    )


def _old(table: str) -> tuple[AbiSnapshot, Function]:
    fn = Function(
        name="foo",
        mangled="foo",
        return_type="void",
        declared_in_headers_fact=Fact.present(True),
        in_public_contract_fact=Fact.present(True),
        binary_exported_fact=OLD_FACT[table],
    )
    snap = AbiSnapshot(
        library="libx.so",
        version="1",
        functions=[fn],
        elf=_elf(table),
        platform=None if table == "no_binary" else "elf",
    )
    return snap, fn


def _new_decl() -> Function:
    """NEW still declares foo, with no contract evidence and no export."""
    return Function(
        name="foo",
        mangled="foo",
        return_type="void",
        declared_in_headers_fact=Fact.present(True),
        binary_exported_fact=Fact.present(False),
    )


def test_oracle_is_not_constant() -> None:
    assert set(EXPECT_GAP.values()) == {True, False}


@pytest.mark.parametrize(
    ("table", "typed"), list(itertools.product(TABLES, (True, False)))
)
def test_old_absence_must_be_established(table: str, typed: bool) -> None:
    snap, old = _old(table)
    table_arg = (
        observed_export_table(snap, ())
        if typed
        else frozenset(s.name for s in (snap.elf.symbols if snap.elf else ()))
    )
    got = surface_exit_is_evidence_gap(
        old, _new_decl(), old_exported_symbols=table_arg, key="foo"
    )
    expected = EXPECT_GAP[table]
    assert got is expected, (table, typed)


def _pair(table: str) -> tuple[AbiSnapshot, AbiSnapshot]:
    old, _ = _old(table)
    new = AbiSnapshot(
        library="libx.so",
        version="2",
        functions=[_new_decl()],
        elf=ElfMetadata(symbols=[ElfSymbol(name="bar")], machine="EM_X86_64"),
        platform="elf",
    )
    return old, new


@pytest.mark.parametrize("table", ["unread", "read_with"])
def test_through_compare_an_unread_old_table_never_hides_the_exit(table: str) -> None:
    """End to end: OLD exported foo (its table unread, or read), NEW still
    declares it but no longer exports it. The exit is reported either way."""
    old, new = _pair(table)
    kinds = {c.kind for c in compare(old, new).changes if c.symbol == "foo"}
    assert kinds & {
        ChangeKind.FUNC_VISIBILITY_CHANGED,
        ChangeKind.FUNC_REMOVED,
    }, (table, kinds)


# OLD's own (c) in every form a reader can meet, and whether it establishes
# absence on its own: a confirmed negative does -- including a legacy-derived
# one, which exists only for a pre-v46 record where `Visibility.HIDDEN` was
# the dumper's own table reading. An unknown status never does. Written out.
OLD_OWN_FACT = {
    "observed_false": (Fact.present(False), True),
    "legacy_false": (Fact.partial(False, "derived-from-legacy-visibility"), True),
    "not_collected": (Fact.not_collected("not read"), False),
    "unsupported": (Fact.unsupported("no export table format"), False),
    "failed": (Fact.failed("export table not read"), False),
    "observed_true": (Fact.present(True), False),
}


def _old_with_fact(fact: Fact[bool]) -> tuple[AbiSnapshot, Function]:
    snap, fn = _old("unread")
    fn.binary_exported_fact = fact
    return snap, fn


def test_own_fact_oracle_is_not_constant() -> None:
    assert {ok for _f, ok in OLD_OWN_FACT.values()} == {True, False}


_BUGGY = {("not_collected", False), ("unsupported", False)}


@pytest.mark.parametrize(
    ("fact_name", "typed"),
    [
        pytest.param(
            f,
            t,
            marks=pytest.mark.xfail(
                strict=True, reason="an unknown OLD fact counted as absence"
            )
            if (f, t) in _BUGGY
            else (),
        )
        for f, t in itertools.product(OLD_OWN_FACT, (True, False))
    ],
)
def test_only_an_observed_negative_establishes_old_absence(
    fact_name: str, typed: bool
) -> None:
    """OLD has a binary whose table was not read, so the table itself proves
    nothing: suppression may rest only on OLD's own confirmed negative --
    never on an uncollected, unsupported or failed fact, whether the caller
    passed the typed table or a plain name set (no coverage)."""
    fact, establishes = OLD_OWN_FACT[fact_name]
    snap, old = _old_with_fact(fact)
    table_arg = observed_export_table(snap, ()) if typed else frozenset()
    got = surface_exit_is_evidence_gap(
        old, _new_decl(), old_exported_symbols=table_arg, key="foo"
    )
    assert got is establishes, (fact_name, typed)
