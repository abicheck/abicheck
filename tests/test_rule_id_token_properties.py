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

"""An id-less rule's ``rule_id`` is a portable, stable token (field report H3).

The id used to be ``name=repr(value)`` pairs joined by ``|``, e.g.
``expires=datetime.date(2027, 4, 1)|binding='weak'``: collision-resistant, but
not a token a user can paste into an acknowledgment file, and tied to
Python's ``repr`` spelling. The earlier tests checked only the author's
concern (distinctness); these check the consumer's contract, over generated
rules loaded through the real YAML loader:

1. the id matches ``^[A-Za-z0-9._-]+$``;
2. distinct rules get distinct ids (oracle: the rules' own field mapping,
   not the identity function);
3. the id does not change when the YAML keys are reordered;
4. the id is deterministic across processes and Python versions (a small
   committed golden list).
"""

from __future__ import annotations

import random
import re
from datetime import date
from pathlib import Path

import pytest
import yaml
from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.policy.rule_identity import rule_id_of
from abicheck.policy.suppression.rules import SuppressionList

_TOKEN = re.compile(r"^[A-Za-z0-9._-]+$")

_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc")), min_size=1, max_size=12
)


@st.composite
def _entries(draw: st.DrawFn) -> dict[str, object]:
    """One suppression entry as a YAML mapping. The pattern always carries
    ``|`` and ``=`` -- the separators the old repr-joined form had to guard
    against -- plus arbitrary unicode."""
    entry: dict[str, object] = {
        "symbol_pattern": re.escape(draw(_text)) + "|a=b",
        "reason": draw(_text),
    }
    if draw(st.booleans()):
        entry["label"] = draw(_text)
    if draw(st.booleans()):
        entry["expires"] = draw(
            st.dates(min_value=date(2000, 1, 1), max_value=date(2999, 12, 31))
        ).isoformat()
    if draw(st.booleans()):
        entry["change_kind"] = draw(
            st.sampled_from(["func_removed", "func_removed_elf_only", "var_removed"])
        )
    if draw(st.booleans()):
        entry["binding"] = draw(st.sampled_from(["weak", "global"]))
    if draw(st.booleans()):
        entry["reachability"] = "proven-unreachable-only"
    return entry


def _load(tmp_path: Path, entries: list[dict[str, object]]) -> SuppressionList:
    path = tmp_path / "s.yaml"
    path.write_text(
        yaml.safe_dump(
            {"version": 1, "suppressions": entries},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return SuppressionList.load(path)


def _matching_view(entry: dict[str, object]) -> tuple[tuple[str, object], ...]:
    """The oracle for "distinct rules": the entry's own fields minus prose."""
    return tuple(sorted((k, v) for k, v in entry.items() if k != "reason"))


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(entries=st.lists(_entries(), min_size=1, max_size=6), seed=st.integers(0, 2**16))
def test_rule_id_is_a_token_injective_and_key_order_invariant(
    tmp_path: Path, entries: list[dict[str, object]], seed: int
) -> None:
    ids = [rule_id_of(r) for r in _load(tmp_path, entries).rules]

    for rid in ids:
        assert _TOKEN.match(rid), rid

    views = [_matching_view(e) for e in entries]
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            assert (views[i] == views[j]) == (ids[i] == ids[j]), (
                entries[i],
                entries[j],
            )

    rng = random.Random(seed)
    shuffled = []
    for e in entries:
        items = list(e.items())
        rng.shuffle(items)
        shuffled.append(dict(items))
    assert [rule_id_of(r) for r in _load(tmp_path, shuffled).rules] == ids


#: Generated once; must never change without a changelog entry, since users
#: paste these into acknowledgment files. CI runs this on 3.11/3.13/3.15.
_GOLDEN: tuple[tuple[dict[str, object], str], ...] = (
    (
        {
            "symbol_pattern": ".*",
            "change_kind": "func_removed_elf_only",
            "label": "weak-func-hidden",
            "expires": "2027-04-01",
            "binding": "weak",
            "reason": "r",
        },
        "rule-2f4c127a72d9",
    ),
    ({"symbol": "foo", "reason": "r"}, "rule-099b7530e939"),
    (
        {"symbol_pattern": "a|change_kind=x", "label": "名前", "reason": "r"},
        "rule-7446a548f51f",
    ),
)


@pytest.mark.parametrize(("entry", "expected"), _GOLDEN)
def test_rule_id_golden(
    tmp_path: Path, entry: dict[str, object], expected: str
) -> None:
    (rule,) = _load(tmp_path, [entry]).rules
    assert rule_id_of(rule) == expected


def test_a_user_id_is_kept_verbatim(tmp_path: Path) -> None:
    (rule,) = _load(
        tmp_path, [{"id": "waiver-1", "symbol": "foo", "reason": "r"}]
    ).rules
    assert rule_id_of(rule) == "waiver-1"
