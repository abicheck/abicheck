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

**This module-level "no legacy attribute read" rule is necessary but was
not, by itself, proof of authority (a real finding, not a hypothetical:**
2026-09-05 re-assessment of `docs/contribute/plans/
duplication-and-convergence-assessment.md`). The typedef/constant cohorts'
own selector functions (`compare.typedefs.typedef_index_pair`/
`compare.constants.constant_index_pair`) satisfied this rule from day one —
neither read a forbidden attribute directly — while still building *both*
an IR-backed and a legacy-projected index on every comparison and using the
IR only when it exactly reproduced the legacy projection: a fidelity gate,
not an authority transfer, since a disagreeing IR was never actually
trusted. That gap is not one this AST scan can close on its own (whether a
legacy projection "wins" is a runtime data question, not a static one); it
was closed instead by deleting the dual-index construction and adjudication
itself (ADR-063 Track T3, "typedef/constant authority cutover") —
`typedef_index_pair`/`constant_index_pair` now decide each side
independently, reading the real `SemanticIR` directly whenever *that side*
carries one (never both-or-neither, since a Codex review round found that
rule would starve an IR-carrying side of its own real evidence purely
because the other side lacked any), with no second index built to
compare it against, and a disagreement between a real `SemanticIR` and its
own legacy sidecar identity (`AbiSnapshot.typedef_entity_ids`/
`constant_entity_ids`) is now caught earlier, at snapshot construction
(`AbiSnapshot.__post_init__` ->
`model.semantic_ir_legacy_adapter.assert_typedef_ir_consistent`/
`assert_constant_ir_consistent`), as a hard
`errors.SemanticIrAuthorityError` rather than a silently-absorbed fallback.
Read this module's own AST-scan rule as "no *reachable* legacy read for a
migrated cohort's detector," not as "this cohort's authority is real" —
that second claim needs checking against the actual selector/producer
dependency path, the same way this note now records for typedefs/constants.

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
import re
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
    # ADR-063 6B cohort 3 (record layout): TYPE_SIZE_CHANGED/
    # TYPE_ALIGNMENT_CHANGED read CanonicalEntity.size_bits/alignment_bits
    # through each side's index; a RecordType's own layout attributes and a
    # snapshot's `types` are off limits here. The index module owns the
    # entity-level read (`semantic_ir_record_layout.entity_layout`).
    MigratedCohort(
        name="record_layout",
        modules=("abicheck/compare/record_layout.py",),
        forbidden_attributes=frozenset({"types", "size_bits", "alignment_bits"}),
        adapter="abicheck/model/semantic_ir_legacy_adapter.py",
    ),
    # ADR-063 6B variable cohort: VAR_TYPE_CHANGED/VAR_BECAME_CONST/
    # VAR_LOST_CONST read CanonicalEntity.canonical_spelling/cv_qualification
    # through each side's index; a Variable's own type facts and a snapshot's
    # variables are off limits here. Pairing stays with the caller's
    # SymbolIdentityIndex, and an unnamed variable is projected through the
    # adapter's legacy_variable_occurrences.
    MigratedCohort(
        name="variables",
        modules=("abicheck/compare/variables.py",),
        forbidden_attributes=frozenset(
            {"type", "is_const", "variables", "variable_map"}
        ),
        adapter="abicheck/model/semantic_ir_legacy_adapter.py",
    ),
    # ADR-063 6B function-signature cohort: FUNC_RETURN_CHANGED/
    # FUNC_PARAMS_CHANGED/FUNC_REF_QUAL_CHANGED/FUNC_VARIADIC_* read the five
    # CanonicalEntity signature facts (semantic_ir v3, schema v57) through
    # each side's index; a Function's own signature fields and a snapshot's
    # functions are off limits here. An unnamed function is projected through
    # the adapter's legacy_function_signature_occurrences.
    MigratedCohort(
        name="function_signature",
        modules=("abicheck/compare/function_signature.py",),
        # `ref_qualifier`/`is_variadic` are not listed: they are also the
        # CanonicalEntity facts' own names, which this name-based scan cannot
        # tell apart from the Function fields of the same name.
        forbidden_attributes=frozenset(
            {"return_type", "params", "functions", "function_map"}
        ),
        adapter="abicheck/model/semantic_ir_legacy_adapter.py",
    ),
    # ADR-063 6B declaration-fact cohort: deprecation, access and declared
    # alignment read CanonicalEntity.deprecated/access/declared_alignment_bits.
    # `deprecated`/`access` are also the IR facts' own names, so only the
    # declaration-side `*_fact` spellings and `alignment_bits` are scanned.
    MigratedCohort(
        name="declaration_facts",
        modules=("abicheck/compare/declaration_facts.py",),
        forbidden_attributes=frozenset(
            {
                "deprecated_fact",
                "access_fact",
                "alignment_bits",
                "alignment_bits_fact",
                "variables",
                "functions",
            }
        ),
        adapter="abicheck/model/semantic_ir_legacy_adapter.py",
    ),
    # ADR-063 6B parameter cohort: defaults, renames, pointer levels,
    # restrict, va_list and override read the per-parameter CanonicalEntity
    # facts; snapshot-level producer gates stay with the caller.
    MigratedCohort(
        name="parameter_facts",
        modules=("abicheck/compare/parameter_facts.py",),
        forbidden_attributes=frozenset(
            {
                "params",
                "default",
                "pointer_depth",
                "return_pointer_depth",
                "is_restrict_fact",
                "is_va_list_fact",
                "functions",
                "function_map",
            }
        ),
        adapter="abicheck/model/semantic_ir_legacy_adapter.py",
    ),
    # ADR-063 6B function-lifecycle cohort: inline transitions, deletion and
    # the converting-constructor test read the function occurrence's facts.
    MigratedCohort(
        name="function_lifecycle",
        modules=("abicheck/compare/function_lifecycle.py",),
        forbidden_attributes=frozenset(
            {"is_inline", "deleted_from_dwarf", "params", "functions", "function_map"}
        ),
        adapter="abicheck/model/semantic_ir_legacy_adapter.py",
    ),
    # `functions` is deliberately NOT registered here yet. A first attempt
    # (abicheck/compare/functions.py's function_identity_index) built a
    # SemanticIRIndex per comparison but only ever looked up a function's
    # entity_id and discarded the result -- the resolved identity itself
    # still came from the flat Function object regardless, so the lookup
    # could not affect anything (Codex review, PR #1224). Registering that
    # as a closed cohort would make this gate pass while SemanticIR content
    # still cannot influence function matching -- exactly the false
    # assurance this gate exists to prevent. See
    # docs/_meta/one-semantic-pipeline-status.yaml's `semantic_ir` concept
    # entry (2026-09-11 note) and abicheck/compare/functions.py's own
    # module docstring for the full account of what landed instead (a
    # tested but unconsumed legacy_function_ir adapter projection) and what
    # a genuine cohort 3 would still need.
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


# ---------------------------------------------------------------------------
# Backend-specific declaration representations (ADR-063 6B closure).
#
# Since Phase 10 the snapshot's declarations live inside its SemanticIR, so the
# checker can no longer read a legacy `AbiSnapshot.functions`/`types`
# collection at all. The one *second* representation of declarations a
# snapshot still carries is the debug-format side: `AbiSnapshot.dwarf`
# (`DwarfMetadata.structs`/`enums`, a per-CU layout model with no identity)
# and `AbiSnapshot.dwarf_advanced`. Criterion (4) of the plan's mechanical
# definition of done is "the checker reads no backend-specific collection
# directly", so every checker read of those two fields is recorded below and
# may only shrink: a new reader fails, and an entry whose reads are gone must
# be lowered or deleted. Each remaining entry is either a presence check
# (`has_dwarf`, an evidence tier) or a DWARF-layout detector whose model
# carries no identity to join into the IR (the plan's 2B sweep note).

#: The checker layers this rule scopes to. Producers, storage and model build
#: these representations, so they are out of scope.
CHECKER_GLOBS: tuple[str, ...] = (
    "abicheck/compare/**/*.py",
    "abicheck/policy/**/*.py",
    "abicheck/diff_*.py",
    "abicheck/checker*.py",
    "abicheck/detector*.py",
    "abicheck/post_processing*.py",
    "abicheck/internal_leak.py",
    "abicheck/type_reachability*.py",
    "abicheck/confidence.py",
    "abicheck/analysis_assurance*.py",
    "abicheck/export_surface.py",
    "abicheck/surface*.py",
    "abicheck/contract_*.py",
    "abicheck/idioms.py",
    "abicheck/pattern_verdicts.py",
)

BACKEND_DECLARATION_FIELDS: frozenset[str] = frozenset({"dwarf", "dwarf_advanced"})

#: `(module, enclosing qualname, field) -> read count`: the reviewed baseline.
KNOWN_BACKEND_DECLARATION_READERS: dict[tuple[str, str, str], int] = {
    ("abicheck/analysis_assurance.py", "_dwarf_context_status", "dwarf"): 2,
    ("abicheck/analysis_assurance.py", "_dwarf_context_status", "dwarf_advanced"): 2,
    (
        "abicheck/analysis_assurance_layout.py",
        "layout_unverified_detectors",
        "dwarf",
    ): 2,
    (
        "abicheck/analysis_assurance_layout.py",
        "layout_unverified_detectors",
        "dwarf_advanced",
    ): 2,
    ("abicheck/checker.py", "_diff_advanced_dwarf", "dwarf_advanced"): 4,
    ("abicheck/compare/debug_type_join.py", "join_debug_types", "dwarf"): 1,
    ("abicheck/compare/edge_query.py", "debug_coverage_record", "dwarf"): 2,
    ("abicheck/confidence.py", "_detect_evidence_tiers", "dwarf"): 4,
    ("abicheck/confidence.py", "_detect_evidence_tiers", "dwarf_advanced"): 4,
    ("abicheck/diff_filtering.py", "_enum_canonical_names", "dwarf"): 1,
    ("abicheck/diff_helpers.py", "record_canonical_names", "dwarf"): 1,
    ("abicheck/diff_helpers.py", "typedef_flat_map_is_dwarf_qualified", "dwarf"): 1,
    ("abicheck/diff_long_double.py", "_ld_base_size", "dwarf"): 1,
    ("abicheck/diff_platform.py", "_diff_dwarf", "dwarf"): 2,
    ("abicheck/diff_platform.py", "_has_any_dwarf", "dwarf"): 2,
    ("abicheck/diff_symbols.py", "_is_stripped_symbols_only", "dwarf"): 1,
    ("abicheck/diff_types.py", "_has_type_evidence", "dwarf"): 1,
    ("abicheck/internal_leak.py", "_build_type_map", "dwarf"): 1,
    (
        "abicheck/policy/analysis_assurance_schema_staleness.py",
        "_side_is_stripped_symbols_only",
        "dwarf",
    ): 1,
    (
        "abicheck/policy/depth_projection.py",
        "_strip_header_and_above_evidence",
        "dwarf",
    ): 5,
    (
        "abicheck/policy/depth_projection.py",
        "_structural_facts_are_dwarf_confirmed",
        "dwarf",
    ): 2,
    ("abicheck/surface_graph.py", "_evidence_tier", "dwarf"): 1,
}


def _checker_modules() -> list[Path]:
    return sorted({p for g in CHECKER_GLOBS for p in REPO_ROOT.glob(g)})


def backend_declaration_reads(tree: ast.AST) -> dict[tuple[str, str], int]:
    """`(enclosing qualname, field) -> count` of every read of a
    :data:`BACKEND_DECLARATION_FIELDS` field in *tree*: an attribute access,
    or a `getattr` call through any alias :func:`legacy_collection_reads`
    also resolves."""
    module_aliases = _builtins_module_aliases(tree)
    getattr_aliases = _getattr_aliases(tree, module_aliases)
    counts: dict[tuple[str, str], int] = {}

    def visit(node: ast.AST, qualname: str) -> None:
        for child in ast.iter_child_nodes(node):
            scope = qualname
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope = f"{qualname}.{child.name}" if qualname else child.name
            field_name = None
            if (
                isinstance(child, ast.Attribute)
                and child.attr in BACKEND_DECLARATION_FIELDS
                and (
                    isinstance(child.ctx, ast.Load)
                    or (isinstance(node, ast.AugAssign) and child is node.target)
                )
            ):
                field_name = child.attr
            elif (
                isinstance(child, ast.Call)
                and _is_getattr_call(child, getattr_aliases, module_aliases)
                and len(child.args) >= 2
                and isinstance(child.args[1], ast.Constant)
                and child.args[1].value in BACKEND_DECLARATION_FIELDS
            ):
                field_name = child.args[1].value
            if field_name is not None:
                key = (qualname or "<module>", field_name)
                counts[key] = counts.get(key, 0) + 1
            visit(child, scope)

    visit(tree, "")
    return counts


def current_backend_declaration_readers() -> dict[tuple[str, str, str], int]:
    """Every checker read of a backend-specific declaration field, now."""
    found: dict[tuple[str, str, str], int] = {}
    for path in _checker_modules():
        rel = path.relative_to(REPO_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError:
            continue
        for (qualname, field_name), n in backend_declaration_reads(tree).items():
            name = _unmangled_qualname(qualname)
            if name is None:
                continue
            key = (rel, name, field_name)
            # max, not sum: in a mutmut tree one source function can appear
            # as more than one copy that maps back to its name (observed:
            # every read counted exactly twice), and it is still one reader.
            found[key] = max(found.get(key, 0), n)
    return found


#: mutmut's copy of a function: ``x_<name>__mutmut_<n|orig>`` (module level,
#: so ``_f`` becomes ``x__f__mutmut_orig``) or ``xǁ<Class>ǁ<name>__mutmut_...``
#: (method).
_MUTMUT_NAME = re.compile(r"^x(?:_|ǁ\w+ǁ)(?P<name>\w+?)__mutmut_(?P<which>orig|\d+)$")


def _unmangled_qualname(qualname: str) -> str | None:
    """*qualname* as written in the source, or ``None`` for a mutant copy.

    Run from a mutmut ``mutants/`` tree, every function body exists as
    ``..._mutmut_orig`` plus one numbered copy per mutant: the original is
    the reader the baseline names, and the mutants are not readers at all.
    Without this, the gate reported every baselined reader as new there.
    """
    head, _, last = qualname.rpartition(".")
    match = _MUTMUT_NAME.match(last)
    if match is None:
        return qualname
    if match["which"] != "orig":
        return None
    return f"{head}.{match['name']}" if head else match["name"]


def backend_declaration_problems(
    current: dict[tuple[str, str, str], int],
    baseline: dict[tuple[str, str, str], int],
) -> list[str]:
    """Why *current* disagrees with *baseline*: a read above its baseline
    (a new reader), or a baseline above its reads (a stale entry)."""
    problems: list[str] = []
    for key, n in sorted(current.items()):
        allowed = baseline.get(key, 0)
        if n > allowed:
            rel, qualname, field_name = key
            problems.append(
                f"{rel}: {qualname} reads `AbiSnapshot.{field_name}` {n} time(s) "
                f"(baseline {allowed}). The checker reads declarations through "
                "the snapshot's SemanticIR (ADR-063 6B); a backend-specific "
                "representation is not an input for a new detector. Read "
                "`snapshot.declarations`/the IR, or fold the fact into the IR "
                "at extraction"
            )
    for key, allowed in sorted(baseline.items()):
        if current.get(key, 0) < allowed:
            rel, qualname, field_name = key
            problems.append(
                f"KNOWN_BACKEND_DECLARATION_READERS[{key!r}] = {allowed}, but "
                f"{rel}:{qualname} now reads `{field_name}` "
                f"{current.get(key, 0)} time(s) -- lower the baseline (it only "
                "shrinks)"
            )
    return problems


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
    for problem in backend_declaration_problems(
        current_backend_declaration_readers(), KNOWN_BACKEND_DECLARATION_READERS
    ):
        f.err("semantic-ir-cutover", problem)


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from findings_report import Findings

    findings = Findings()
    check_semantic_ir_cutover(findings)
    return findings.report()


if __name__ == "__main__":
    raise SystemExit(main())
