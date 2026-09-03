#!/usr/bin/env python3
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

"""`semantic-ir-cutover` — a migrated detector cohort may not read back into
the legacy `AbiSnapshot` collection it was migrated off (ADR-063 Phase 6B).

ADR-063's cutover is explicitly incremental: one detector family at a time,
and **each cohort closes with an architecture-gate rule forbidding a direct
legacy-collection read for that family**. Without that closing step a
migration is reversible by accident — the next edit to a migrated module
reaches for `snapshot.typedefs` because every neighbouring module still
does, and the cohort silently un-migrates with nothing failing.

`MIGRATED_COHORTS` is that rule, one entry per closed cohort. It is
deliberately **not** an allowlist-and-shrink baseline like
`IMPORT_CYCLE_ALLOWLIST` or `KNOWN_UNMIGRATED_READERS`: those record
pre-existing debt, whereas a cohort is only added here at the moment it is
migrated, so a grandfathered reader cannot exist. There is no per-site
exemption mechanism on purpose. If a migrated module genuinely needs
something only the legacy shape carries, the answer is to project it inside
`abicheck/model/semantic_ir_legacy_adapter.py` — the one module allowed to
read those collections — not to punch a hole here.

**A real AST scan, not a textual match.** The check resolves the attribute
*base* to decide whether a read is one of the forbidden collections, so:

* `snap.typedefs`, `old.typedefs_qualified`, `self.snapshot.typedefs` are
  flagged (any attribute access whose attribute name is a forbidden one);
* `getattr(snap, "typedefs")` and `getattr(snap, "typedefs", {})` are
  flagged too — the same evasion `fact-field-readers` already learned to
  close, including through a resolved `getattr` alias and through the
  builtin reached off an aliased `builtins` module
  (`import builtins as b; b.getattr(snap, "typedefs")`);
* a *local variable* named `typedefs` is not flagged (it is a `Name`, not
  an `Attribute`), because renaming a local is not un-migrating anything;
* a keyword argument spelled `typedefs=` is not flagged, since that is the
  adapter's own parameter being passed *in*, which is exactly the
  supported direction.

Run via `python scripts/check_ai_readiness.py` (the `semantic-ir-cutover`
check) or directly for a standalone report.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG = REPO_ROOT / "abicheck"


@dataclass(frozen=True)
class MigratedCohort:
    """One closed cutover cohort.

    *modules* are repo-relative paths that must read only through
    `SemanticIRIndex`; *forbidden_attributes* are the `AbiSnapshot` fields
    that cohort was migrated off. *adapter* names the one module permitted
    to read them, quoted back in the failure message so the fix is obvious
    from the error alone.
    """

    name: str
    modules: tuple[str, ...]
    forbidden_attributes: frozenset[str]
    adapter: str


#: Every cohort migrated onto `SemanticIRIndex` so far. Add an entry in the
#: same PR that migrates a family — never later, and never with an
#: exemption.
MIGRATED_COHORTS: tuple[MigratedCohort, ...] = (
    MigratedCohort(
        name="typedefs",
        modules=("abicheck/compare/typedefs.py",),
        forbidden_attributes=frozenset(
            {"typedefs", "typedefs_qualified", "typedef_entity_ids"}
        ),
        adapter="abicheck/model/semantic_ir_legacy_adapter.py",
    ),
    MigratedCohort(
        name="constants",
        modules=("abicheck/compare/constants.py",),
        forbidden_attributes=frozenset({"constants", "constant_entity_ids"}),
        adapter="abicheck/model/semantic_ir_legacy_adapter.py",
    ),
)

#: `getattr` spellings that reach an attribute without an `ast.Attribute`
#: node. Resolved through a module-level alias too (`from builtins import
#: getattr as g`), matching how `fact_field_readers.py` handles the same
#: evasion.
_GETATTR_NAMES = frozenset({"getattr"})


def _builtins_module_aliases(tree: ast.AST) -> frozenset[str]:
    """Every local name bound to the `builtins` module itself in *tree*
    (`import builtins`, `import builtins as b`), so `b.getattr(...)` is
    recognized as the same evasion as a bare `getattr(...)` call.

    A plain `getattr(obj, "name")` call is an `ast.Call` whose `func` is an
    `ast.Name` -- but nothing stops a caller from reaching the identical
    builtin through an attribute instead
    (`import builtins as b; b.getattr(snap, "typedefs")`), which is an
    `ast.Call` whose `func` is an `ast.Attribute`. `_getattr_aliases` alone
    never sees that shape, since it only tracks names bound to the
    `getattr` *function*, not names bound to the *module* it lives on.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "builtins":
                    aliases.add(alias.asname or alias.name)
    return frozenset(aliases)


def _getattr_aliases(tree: ast.AST, module_aliases: frozenset[str]) -> frozenset[str]:
    """Every local name bound to `getattr` in *tree*, plus `getattr` itself.

    Covers `from builtins import getattr as g`, a plain module-level
    `g = getattr`, and `g = b.getattr` where `b` is a resolved *module* alias
    from `_builtins_module_aliases` -- the assignment counterpart of the
    `b.getattr(...)` call shape `_is_getattr_call` already resolves
    (CodeRabbit review on PR #1041): without this, `import builtins as b;
    g = b.getattr; g(snap, "typedefs")` reached neither branch, since the
    call itself is a bare `Name` (`g(...)`) with no attribute for
    `_is_getattr_call`'s own module-alias check to see, and the assignment
    that produced `g` was an `ast.Attribute` value this function didn't
    recognize as a `getattr` source. A name rebound to something else
    entirely is not tracked -- this resolves aliases *to* `getattr`, it does
    not prove a name is still `getattr` at the call site, which is the same
    (deliberately conservative, over-inclusive) posture the sibling scanners
    take.
    """
    aliases = set(_GETATTR_NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "builtins":
            for alias in node.names:
                if alias.name == "getattr":
                    aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            value = node.value
            resolves_getattr = (
                isinstance(value, ast.Name) and value.id in aliases
            ) or (
                isinstance(value, ast.Attribute)
                and value.attr == "getattr"
                and isinstance(value.value, ast.Name)
                and value.value.id in module_aliases
            )
            if resolves_getattr:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases.add(target.id)
    return frozenset(aliases)


def _is_getattr_call(
    node: ast.Call, getattr_aliases: frozenset[str], module_aliases: frozenset[str]
) -> bool:
    """Whether *node* invokes `getattr` under any resolved spelling: a bare
    `getattr(...)`/alias call (`func.id` resolved against *getattr_aliases*),
    or `<builtins-module-alias>.getattr(...)` (`func.value.id` resolved
    against *module_aliases*, the names bound to the `builtins` module
    itself)."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in getattr_aliases
    if isinstance(func, ast.Attribute) and func.attr == "getattr":
        base = func.value
        return isinstance(base, ast.Name) and base.id in module_aliases
    return False


def legacy_collection_reads(
    tree: ast.AST, forbidden: frozenset[str]
) -> list[tuple[int, str]]:
    """Every `(lineno, attribute)` read of a *forbidden* collection in *tree*.

    Both spellings a real evasion would use: a direct attribute access, and
    a `getattr(obj, "<name>")` call through any resolved alias -- including
    the same builtin reached through an attribute
    (`import builtins as b; b.getattr(obj, "<name>")`), not only a bare
    `Name` call. A bare `Name` attribute is never reported -- a local
    variable that happens to share the field's name is not a read of the
    field.
    """
    module_aliases = _builtins_module_aliases(tree)
    getattr_aliases = _getattr_aliases(tree, module_aliases)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden:
            found.append((node.lineno, node.attr))
        elif isinstance(node, ast.Call):
            if not _is_getattr_call(node, getattr_aliases, module_aliases):
                continue
            if len(node.args) < 2:
                continue
            name_arg = node.args[1]
            if (
                isinstance(name_arg, ast.Constant)
                and isinstance(name_arg.value, str)
                and name_arg.value in forbidden
            ):
                found.append((node.lineno, name_arg.value))
    return sorted(set(found))


def check_semantic_ir_cutover(f) -> None:  # noqa: ANN001 - Findings, see caller
    """ERROR if a migrated cohort's module reads a legacy collection it was
    migrated off (see this module's docstring)."""
    for cohort in MIGRATED_COHORTS:
        for rel in cohort.modules:
            path = REPO_ROOT / rel
            if not path.exists():
                f.err(
                    "semantic-ir-cutover",
                    f"{rel}: listed in MIGRATED_COHORTS[{cohort.name!r}] but "
                    "does not exist -- a cohort's module list must name real "
                    "files, or the rule silently enforces nothing",
                )
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except SyntaxError:
                continue
            for lineno, attr in legacy_collection_reads(
                tree, cohort.forbidden_attributes
            ):
                f.err(
                    "semantic-ir-cutover",
                    f"{rel}:{lineno}: reads the legacy `AbiSnapshot.{attr}` "
                    f"collection, but the {cohort.name!r} detector cohort was "
                    "migrated onto SemanticIRIndex (ADR-063 Phase 6B) and must "
                    "read only through it. Project what you need inside "
                    f"{cohort.adapter} -- the one module allowed to read this "
                    "collection -- and read it back through the index. There "
                    "is deliberately no per-site exemption: this cohort is "
                    "freshly migrated, so a grandfathered reader cannot exist",
                )


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from findings_report import Findings

    findings = Findings()
    check_semantic_ir_cutover(findings)
    return findings.report()


if __name__ == "__main__":
    raise SystemExit(main())
