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
"""``ExtractorRecord.source_side`` replaces the name-prefix check the policy
layer used to run. The flag is derived from the record's name at construction,
so every producer gets it with no per-call-site edit. These tests pin which
producers match, and that the flag reaches policy and stored packs unchanged."""

from __future__ import annotations

import ast
from pathlib import Path

from abicheck.buildsource.model import (
    SOURCE_SIDE_EXTRACTOR_PREFIXES,
    ExtractorRecord,
    is_source_side_extractor_name,
)

ROOT = Path(__file__).resolve().parents[1]
BUILDSOURCE = ROOT / "abicheck" / "buildsource"


def _producer_calls() -> list[tuple[str, int, ast.Call]]:
    out: list[tuple[str, int, ast.Call]] = []
    for path in sorted(BUILDSOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ExtractorRecord"
            ):
                out.append((str(path.relative_to(ROOT)), node.lineno, node))
    return out


def _name_arg(call: ast.Call) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == "name":
            return kw.value
    return call.args[0] if call.args else None


def _literal_head(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (
        isinstance(node, ast.JoinedStr)
        and node.values
        and isinstance(node.values[0], ast.Constant)
    ):
        return str(node.values[0].value)
    return None


def _bound_literals(where: str, var: str) -> list[str]:
    """Every string literal *var* is assigned in *where*'s module."""
    tree = ast.parse((ROOT / where).read_text(encoding="utf-8"))
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == var for t in targets):
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                values.append(value.value)
    return values


def test_only_the_expected_producers_match_the_rule() -> None:
    calls = _producer_calls()
    assert calls, "no ExtractorRecord producers found -- scan is broken"
    matched_literals: set[str] = set()
    manifest_driven = 0
    for where, lineno, call in calls:
        # One classification point: no producer may pass the flag itself.
        assert all(kw.arg != "source_side" for kw in call.keywords), (
            f"{where}:{lineno} sets source_side explicitly; derive it from the name"
        )
        name = _name_arg(call)
        assert name is not None, f"{where}:{lineno} builds a record without a name"
        head = _literal_head(name)
        if head is not None:
            if is_source_side_extractor_name(head):
                matched_literals.add(head)
            continue
        if isinstance(name, ast.Name):
            # A local bound only to fixed literals: none may match the rule.
            bound = _bound_literals(where, name.id)
            assert bound, f"{where}:{lineno}: no literal binding for {name.id}"
            matched = [b for b in bound if is_source_side_extractor_name(b)]
            assert not matched, f"{where}:{lineno} binds {matched}"
            continue
        manifest_driven += 1  # an external manifest's own name, classified at runtime

    # The producers the old prefix check matched. A removed or renamed producer
    # shows up here rather than silently dropping out of the assurance note.
    assert matched_literals == {"compile_commands", "source_abi:hybrid", "source_abi:"}
    assert manifest_driven == 2


def test_rule_is_the_prefix_set_policy_used_to_match() -> None:
    assert SOURCE_SIDE_EXTRACTOR_PREFIXES == ("source_abi", "compile_", "source_graph")
    assert is_source_side_extractor_name("compile_commands")
    assert is_source_side_extractor_name("source_abi:clang")
    assert is_source_side_extractor_name("source_graph")
    assert not is_source_side_extractor_name("compile-db")  # hyphen, not underscore
    assert not is_source_side_extractor_name("build_query")
    assert not is_source_side_extractor_name("call_graph:clang")


def test_construction_derives_the_flag_from_the_name() -> None:
    assert ExtractorRecord(name="compile_commands").source_side is True
    assert ExtractorRecord(name="source_abi:hybrid").source_side is True
    assert ExtractorRecord(name="build_query").source_side is False
    # An explicit value is kept as given.
    assert ExtractorRecord(name="build_query", source_side=True).source_side is True


def test_round_trip_keeps_the_flag_and_emits_it_only_when_set() -> None:
    flagged = ExtractorRecord(name="compile_commands", status="partial")
    plain = ExtractorRecord(name="build_query", status="partial")
    assert flagged.to_dict()["source_side"] is True
    assert "source_side" not in plain.to_dict()
    assert ExtractorRecord.from_dict(flagged.to_dict()).source_side is True
    assert ExtractorRecord.from_dict(plain.to_dict()).source_side is False


def test_stored_record_without_the_key_is_classified_by_the_same_rule() -> None:
    # A pack written before the field existed must keep its assurance meaning.
    legacy_source = {"name": "compile_commands", "status": "partial"}
    legacy_other = {"name": "build_query", "status": "partial"}
    assert ExtractorRecord.from_dict(legacy_source).source_side is True
    assert ExtractorRecord.from_dict(legacy_other).source_side is False
    # An explicit key always wins over the name.
    explicit = {"name": "build_query", "status": "partial", "source_side": True}
    assert ExtractorRecord.from_dict(explicit).source_side is True
