# SPDX-License-Identifier: Apache-2.0
"""The source scope whose semantics ``EXTRACTOR_GENERATION`` and
``RESOLVER_GENERATION`` (``storage/versioning.py``) stand for (ADR-062 D2).

A generation is a claim: "packages stamped with N were produced by semantics
N". Nothing else in the repository makes that claim checkable, so this module
names the files that define those semantics and records a content fingerprint
of them in ``generation_fingerprints.json`` next to the generation it was
recorded at. ``tests/unit/storage/test_generation_fingerprint.py`` fails when
the scoped source changes without a re-record, and
``scripts/record_generation_fingerprints.py`` is the one way to re-record:
``--bump`` after a generation increment, or ``--no-semantic-change --reason``
when a scoped edit was a refactor that changes no recorded or derived fact.

Scope is deliberately narrow so unrelated edits never fail CI:

* **Extractor**: ``abicheck/extract/**`` plus the root-level parse-stage
  modules the root ``CLAUDE.md`` names (``*_metadata.py``, ``dwarf_*.py``,
  ``pdb_*.py``, ``dumper.py``). Renderers, CLI wiring, policy and tests are out.
  The source tier (``abicheck/buildsource/``) is not in this axis.
* **Resolver**: an explicit list of the modules that decide provider
  selection, alias normalization, symbol-version handling and reachability.
  Every other module whose name says ``resol``/``reachab``/``alias``/
  ``provider`` must appear in :data:`RESOLVER_NON_SEMANTIC_FILES` with a
  reason, so a new resolver cannot be left out silently.
* **Source** (ADR-062 D2 amendment): ``abicheck/buildsource/source_extractors/``
  (the L4 backends) plus the explicit :data:`SOURCE_TIER_FILES` (the L4
  replay/link and L5 graph producers). It is its own axis, so an L4/L5 edit
  does not drift an L2 package on the extractor axis, and a package records a
  source generation only when it carries source-tier facts.

What it cannot prove: a refactor and a semantic change inside the scope look
the same to a hash. The re-record step is therefore an explicit, reviewed
claim, not an inference.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from .code_identity import PACKAGE_ROOT

__all__ = [
    "EXTRACTOR_ROOT_GLOBS",
    "RECORD_PATH",
    "REPO_ROOT",
    "RESOLVER_NAME_WORDS",
    "RESOLVER_NON_SEMANTIC_FILES",
    "RESOLVER_SEMANTIC_FILES",
    "SOURCE_EXTRACTOR_DIR",
    "SOURCE_TIER_FILES",
    "file_digests",
    "fingerprint",
    "scope_files",
]

#: The repository root, the parent of the installed ``abicheck`` package.
REPO_ROOT = PACKAGE_ROOT.parent

#: Where the recorded fingerprints live, beside this module.
RECORD_PATH = Path(__file__).with_name("generation_fingerprints.json")

#: Root-level modules that parse binaries and debug/symbol data (the "Parse"
#: stage of the root ``CLAUDE.md``). Matched by name inside ``abicheck/``.
EXTRACTOR_ROOT_GLOBS: tuple[str, ...] = (
    "*_metadata.py",
    "dwarf_*.py",
    "pdb_*.py",
    "dumper.py",
)

#: A module whose file stem contains one of these words is resolver-named and
#: must be classified below (semantic or non-semantic with a reason).
RESOLVER_NAME_WORDS: tuple[str, ...] = ("resol", "reachab", "alias", "provider")

#: Modules that decide the semantics of a resolver generation.
RESOLVER_SEMANTIC_FILES: tuple[str, ...] = (
    "abicheck/workflows/elf_dependency_resolver.py",
    "abicheck/debug_resolver.py",
    "abicheck/bundle_resolution_reachability.py",
    "abicheck/post_processing_reachability.py",
    "abicheck/type_reachability.py",
    "abicheck/type_reachability_spelling.py",
    "abicheck/type_reachability_stdlib_spellings.py",
    "abicheck/policy/use_case_reachability.py",
    "abicheck/buildsource/source_extractors/resolver.py",
    "abicheck/buildsource/providers.py",
    "abicheck/compare/export_owner_resolution.py",
    "abicheck/storage/native_identity_aliases.py",
    "abicheck/workflows/compare/resolve.py",
)

#: The directory whose every module is in the source-tier scope: the L4 source
#: extractors (ADR-030 D3). Derived from the tree, so a new backend is in scope
#: without a list edit.
SOURCE_EXTRACTOR_DIR = "abicheck/buildsource/source_extractors"

#: The L4 replay/link producers and the L5 graph producers and folds that decide
#: what the source tier records for a build tree (ADR-062 D2 amendment). These
#: are the modules a change to ``SOURCE_GENERATION`` must cover. Comparison and
#: finding modules (``source_diff``, ``source_graph_compare``,
#: ``source_graph_findings``, ``cross_source_checks*``) decide verdicts from
#: recorded facts, not what is recorded, so they are out of scope.
SOURCE_TIER_FILES: tuple[str, ...] = (
    # L4: replay and per-TU/per-library surface
    "abicheck/buildsource/source_abi.py",
    "abicheck/buildsource/source_link.py",
    "abicheck/buildsource/source_replay.py",
    "abicheck/buildsource/source_replay_shared.py",
    "abicheck/buildsource/source_replay_pool.py",
    "abicheck/buildsource/clang_ast_run.py",
    # L5: the shared clang AST pass and the graph families it folds
    "abicheck/buildsource/l5_ast_pass.py",
    "abicheck/buildsource/l5_ast_run.py",
    "abicheck/buildsource/inline_graph_fold.py",
    "abicheck/buildsource/semantic_fact_folds.py",
    "abicheck/buildsource/call_graph.py",
    "abicheck/buildsource/template_class_origin.py",
    "abicheck/buildsource/template_graph.py",
    "abicheck/buildsource/template_graph_fold.py",
    "abicheck/buildsource/template_graph_value_decls.py",
    "abicheck/buildsource/macro_graph.py",
    "abicheck/buildsource/override_graph.py",
    "abicheck/buildsource/type_graph.py",
    "abicheck/buildsource/type_graph_index.py",
    "abicheck/buildsource/include_graph.py",
    "abicheck/buildsource/include_graph_workers.py",
    "abicheck/buildsource/source_graph.py",
    "abicheck/buildsource/source_graph_build_source_abi.py",
)

#: Resolver-named modules that do not decide resolver semantics, each with the
#: reason a reviewer can check against the file.
RESOLVER_NON_SEMANTIC_FILES: dict[str, str] = {
    "abicheck/cli_resolve.py": "CLI argument resolution; reads no facts and derives none",
    "abicheck/workflows/pack_resolution.py": (
        "policy-pack configuration shaping for the compare front ends; "
        "decides which pack fields apply, derives no snapshot fact or graph"
    ),
    "abicheck/compatibility_evaluation_resolver.py": (
        "verdict resolution over results already derived (policy layer)"
    ),
    "abicheck/model/entity_resolver.py": (
        "entity-id lookup used to match old and new entities, not a derived "
        "resolution graph"
    ),
    "abicheck/frontends/action/tag_resolution.py": (
        "Action tag selection for releases; not fact or graph semantics"
    ),
    "abicheck/workflows/aggregate/expected_targets.py": (
        "chooses which aggregate inputs to read; does not interpret their contents"
    ),
    "abicheck/workflows/artifact/resolved_plan.py": (
        "the ResolvedArtifactPlan value type: the shape of a resolved plan, no resolution logic (resolve.py decides it)"
    ),
    "abicheck/workflows/artifact/resolve.py": (
        "chooses which artifact inputs to read; does not interpret their contents"
    ),
    "abicheck/workflows/input_resolution.py": (
        "chooses which input paths to read; does not interpret their contents"
    ),
    "abicheck/workflows/resolved_execution_context.py": (
        "execution-context wiring for a resolved request; no fact semantics"
    ),
    "abicheck/compare/elf_alias_pairs.py": (
        "pairs already-extracted ELF symbols by version tag for the symbol diff; "
        "resolves no artifact, owner or reachability (the extracted binding and "
        "visibility facts it reads are in the extractor scope)"
    ),
}


def _rel(path: Path) -> str:
    """Repo-relative POSIX path of *path*."""
    return path.relative_to(REPO_ROOT).as_posix()


def _extractor_files() -> tuple[str, ...]:
    """Every extractor-scope file, repo-relative and sorted."""
    found: set[Path] = set((PACKAGE_ROOT / "extract").rglob("*.py"))
    for pattern in EXTRACTOR_ROOT_GLOBS:
        found.update(PACKAGE_ROOT.glob(pattern))
    return tuple(
        sorted(_rel(p) for p in found if p.is_file() and "__pycache__" not in p.parts)
    )


def _source_files() -> tuple[str, ...]:
    """Every source-tier file, repo-relative and sorted.

    The ``source_extractors/`` directory is derived from the tree, so a new
    backend is in scope without a list edit. :data:`SOURCE_TIER_FILES` is
    explicit.
    """
    found: set[str] = set(SOURCE_TIER_FILES)
    found.update(
        _rel(p)
        for p in (REPO_ROOT / SOURCE_EXTRACTOR_DIR).rglob("*.py")
        if p.is_file() and "__pycache__" not in p.parts
    )
    # A module that decides resolver semantics keeps its one owner: the
    # resolver axis. ``source_extractors/resolver.py`` is such a module.
    found.difference_update(RESOLVER_SEMANTIC_FILES)
    return tuple(sorted(found))


def scope_files(axis: str) -> tuple[str, ...]:
    """The repo-relative source files that define *axis*'s semantics, sorted.

    ``axis`` is ``"extractor"``, ``"resolver"`` or ``"source"``. The extractor
    set is derived from the tree, so a new ``extract/`` module is in scope
    without a list edit. The resolver set is the explicit
    :data:`RESOLVER_SEMANTIC_FILES`. The source set is
    :func:`_source_files`. The three sets are disjoint, so an L4/L5 edit does
    not move the extractor axis and an extractor edit does not move the source
    axis.
    """
    if axis == "extractor":
        return _extractor_files()
    if axis == "resolver":
        return tuple(sorted(RESOLVER_SEMANTIC_FILES))
    if axis == "source":
        return _source_files()
    raise ValueError(f"unknown generation axis {axis!r}")


def file_digests(paths: Iterable[str]) -> dict[str, str]:
    """``{repo-relative path: sha256 of its content}``, ``"MISSING"`` if absent.

    Content is hashed with CRLF folded to LF, so a checkout that converts line
    endings (Windows with autocrlf) yields the same digest as the LF original.
    A lone CR is content and is left alone. A missing file is recorded rather
    than dropped, so a deleted or renamed scoped module is a change, not a
    silently smaller scope.
    """
    digests: dict[str, str] = {}
    for rel in paths:
        path = REPO_ROOT / rel
        if path.is_file():
            content = path.read_bytes().replace(b"\r\n", b"\n")
            digests[rel] = hashlib.sha256(content).hexdigest()
        else:
            digests[rel] = "MISSING"
    return digests


def fingerprint(digests: Mapping[str, str]) -> str:
    """One hash over ``{path: digest}``; order-independent, path-sensitive."""
    h = hashlib.sha256(b"abicheck-generation-fingerprint-v1\0")
    for rel in sorted(digests):
        h.update(rel.encode() + b"\0" + digests[rel].encode() + b"\0")
    return h.hexdigest()


def load_record() -> dict[str, object]:
    """The recorded fingerprints, or ``{}`` before the first recording."""
    if not RECORD_PATH.is_file():
        return {}
    data = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def write_record(data: Mapping[str, object]) -> None:
    """Write the record in the stable form the diff review reads."""
    RECORD_PATH.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
