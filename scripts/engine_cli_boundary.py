#!/usr/bin/env python3
"""Engine/CLI dependency-direction gate (Phase 0 of
docs/contribute/plans/duplication-and-convergence-assessment.md).

A leaf module imported by ``check_ai_readiness.py``, which registers
:func:`check_engine_cli_boundary` as the ``engine-cli-boundary`` check. Split
out rather than added inline because ``check_ai_readiness.py`` is already
past the 2000-line hard cap and only stays green through
``LARGE_FILE_ALLOWLIST`` -- which AGENTS.md is explicit is "not a way to
silently exempt a new file" -- mirroring the same extraction
``adr_status_sync.py`` already established for the ADR-status gate.

Pure-stdlib, like its caller, so it can run as the first CI step before
``pip install``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "abicheck"


class Findings(Protocol):
    """The error/warning sink check_ai_readiness.py passes in."""

    def err(self, check: str, msg: str) -> None:
        """Record a blocking finding under `check`."""
        ...

    def warn(self, check: str, msg: str) -> None:
        """Record a non-blocking finding under `check`."""
        ...


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


# Engine-layer trees: the shared compare/scan engine, the Tier-2 service
# layer, the artifact-application service (the flat ``artifact_*.py``
# family, plus its ADR-061 Phase 3 migrated home under
# ``workflows/artifact/``), and the build-source evidence package. None of
# these may import ``click`` or a
# ``cli_*`` sibling module — that's a CLI concept leaking into a layer both
# the CLI and the typed Python API depend on, which is exactly the
# inversion `scan_engine.py` importing `click` and raising
# `click.ClickException` already demonstrates. A frontend (`cli*.py`,
# `compat/cli.py`) is on the *other* side of this boundary and is
# deliberately not covered here — it may import engine modules freely.
_ENGINE_MODULE_BASENAMES: frozenset[str] = frozenset({"scan_engine.py"})

#: Package-rooted engine-layer trees, matched as a ``tail.startswith(...)``
#: prefix. ``workflows/artifact/`` is ADR-061 Phase 3's migrated home for
#: what was the flat ``artifact_*.py`` family (``abicheck/artifact_plan.py``
#: moved to ``abicheck/workflows/artifact/contracts.py``) -- without this
#: entry, the migrated module's own docstring claim that "the
#: engine-cli-boundary gate would reject" a ``click``/``cli_*`` import there
#: would be false, since the basename-only check below never matches a
#: module living under a subdirectory (Codex review, fresh evidence).
_ENGINE_PACKAGE_PREFIXES: tuple[str, ...] = ("buildsource/", "workflows/artifact/")


def _is_engine_module(rel: str) -> bool:
    """True if *rel* (posix, relative to repo root) is in the engine layer.

    ``abicheck/scan_engine.py``, every ``abicheck/service*.py``, every
    ``abicheck/artifact_*.py`` (the artifact-application service Phase 1 of
    the convergence plan introduces -- doesn't exist yet, covered pre-
    emptively so this predicate doesn't need a second edit the day it
    lands), every ``abicheck/buildsource/**/*.py``, and every
    ``abicheck/workflows/artifact/**/*.py`` (that same artifact-application
    contract's ADR-061 Phase 3 migrated home) -- see this module's own
    docstring comment for why these trees specifically.
    """
    if not rel.startswith("abicheck/"):
        return False
    tail = rel[len("abicheck/") :]
    if tail in _ENGINE_MODULE_BASENAMES:
        return True
    if "/" not in tail and tail.endswith(".py"):
        basename = tail[: -len(".py")]
        if basename.startswith("service") or basename.startswith("artifact_"):
            return True
    if tail.endswith(".py") and tail.startswith(_ENGINE_PACKAGE_PREFIXES):
        return True
    return False


# Sites deliberately exempted — a real, already-existing inversion this
# check's own baseline records rather than hides, per Phase 0's
# "allowlist-and-shrink" design (mirrors `IMPORT_CYCLE_ALLOWLIST`'s
# philosophy): the list must only shrink, and a new entry needs the same
# review bar as extending that allowlist does (CLAUDE.md's "Don't extend
# IMPORT_CYCLE_ALLOWLIST..." rule applies here identically). See
# docs/contribute/plans/duplication-and-convergence-assessment.md's
# "P1 — Dependency direction and CLI leakage" section and its Phase 1 for
# what closes each of these.
#
# Keyed `"<rel>::<violation-description>::<occurrence>"`, NOT
# `"<rel>:<lineno>"`: a line-number key breaks on any unrelated edit above
# the import (a docstring tweak, a new helper function) even though the
# violation itself hasn't moved, which would force a routine, unrelated
# edit to this file to also touch this allowlist. `occurrence` is the
# violation's 1-based rank among identically-described violations in the
# same file, in top-to-bottom (line) order — stable unless a new,
# identically-shaped import is inserted earlier in the same file, which is
# the one case where re-numbering is actually correct (see
# `service_scan.py`'s three `from .cli_scan_baseline import ...` entries
# below — a real case this format has to disambiguate).
ENGINE_CLI_BOUNDARY_ALLOWLIST: frozenset[str] = frozenset(
    {
        "abicheck/scan_engine.py::import click::1",
        "abicheck/scan_engine.py::from .cli_scan_baseline import ...::1",
        "abicheck/scan_engine.py::from .cli_scan_helpers import ...::1",
        "abicheck/service_scan.py::import click::1",
    }
)


def _is_cli_component(name: str) -> bool:
    """Is *name* — one dotted-path component, or an imported alias's own
    name — a CLI-frontend module spelling? Matches both the top-level
    ``cli_*.py`` sibling family (``cli_dump_helpers``, ...) and a bare
    ``cli`` component, which catches a *nested* CLI adapter package like
    ``abicheck/compat/cli.py`` (imported as `.compat.cli`/`abicheck.compat
    .cli`/`from .compat import cli`) that the top-level-only ``cli_*``
    pattern would otherwise miss entirely."""
    return name == "cli" or name.startswith("cli_")


def _alias_is_real_submodule(base_dir: Path, alias_name: str) -> bool:
    """Does *alias_name* actually name a submodule file/package under
    *base_dir* on disk — as opposed to an ordinary symbol (function,
    constant, class) that merely happens to be *spelled* like a CLI module?
    Only a genuine submodule import creates the engine-to-CLI-frontend
    dependency this check exists to catch: ``from .model import
    cli_default`` importing a plain constant named ``cli_default`` is not a
    CLI dependency at all, even though the imported name starts with
    ``cli_``. A dotted *module path* (`mod` itself, e.g. the `.compat.cli`
    in `from .compat.cli import x`) needs no such check — Python requires
    every component of a dotted import path to already be a real
    module/package, so that part is unambiguous by construction; it's only
    the *trailing* imported name in `from X import name` that could be
    either a submodule or an ordinary symbol.

    A plain directory (no ``__init__.py``) counts too: PEP 420 makes any
    such directory a valid, importable namespace package, so ``from .
    import cli_tools`` for a directory-only ``abicheck/cli_tools/`` is a
    real CLI-frontend dependency Python would actually resolve, not a
    false positive to guard against.

    A same-named file/directory on disk is still not sufficient on its own:
    ``from pkg import name`` resolves to ``pkg``'s own attribute *before*
    Python ever tries importing ``pkg.name`` as a submodule (the reverse of
    the namespace-package case above) -- so a package whose own
    ``__init__.py`` binds ``alias_name`` to an ordinary function/class/value
    shadows a same-named submodule file that never actually gets imported by
    this statement. See ``_package_shadows_attribute``."""
    is_submodule = (base_dir / f"{alias_name}.py").is_file() or (
        base_dir / alias_name
    ).is_dir()
    return is_submodule and not _package_shadows_attribute(base_dir, alias_name)


def _package_shadows_attribute(base_dir: Path, alias_name: str) -> bool:
    """Does *base_dir*'s own ``__init__.py`` bind *alias_name* to an
    ordinary attribute (a function, class, or plain value) at module scope?

    If so, ``from <base_dir's package> import alias_name`` resolves to that
    attribute -- Python checks the already-executed package object for the
    name *before* attempting to import a same-named submodule -- so a
    same-named ``cli``/``cli_*`` submodule file sitting alongside it is
    never actually reached by this particular import statement. Recognizes
    ``Assign`` (including a name bound via tuple/list destructuring, e.g.
    ``cli, other = values`` -- see ``_assign_target_binds_name``)/
    ``AnnAssign`` (with a value)/``FunctionDef``/``ClassDef`` bindings and
    an ``ImportFrom`` re-exporting an ordinary symbol under
    this name from elsewhere (``from .api import cli``, or a *different*
    submodule aliased to this name, ``from . import othername as cli``):
    those unambiguously bind to something other than the real submodule,
    unlike ``from . import cli`` (no module, no rename) inside
    ``__init__.py``, which binds the name to the submodule object itself
    and so isn't a real shadow at all -- nor is ``from .cli import anything
    [as X]`` (source module equal to *alias_name*), since importing *any*
    name out of a module first requires Python to load/execute that module,
    regardless of which symbol is pulled or what it's locally renamed to.
    A bare annotation with no value
    (``cli: object``) is deliberately excluded -- it only records an
    ``__annotations__`` entry and creates no runtime attribute at all, so
    it can't shadow anything either. A namespace package (no
    ``__init__.py``) can't shadow anything this way. One further exception:
    an ``Assign``/value-carrying-``AnnAssign`` whose right-hand side is
    exactly a bare name previously bound by ``from . import alias_name [as
    X]`` -- i.e. an explicit re-export of the real submodule through an
    intermediate alias, such as ``from . import cli as _cli; cli = _cli``
    -- still resolves ``alias_name`` to the submodule object itself, not to
    an unrelated attribute, so it must not be treated as a shadow either.
    Only this one restricted pattern is recognized: a general expression
    (a call, an attribute access, ``importlib.import_module(...)``, ...)
    cannot be proven to name the submodule from syntax alone, so it is
    conservatively still treated as shadowing, per this module's
    false-negative-over-false-positive default for the boundary gate.

    A plain ``ast.Import`` (``import X [as alias_name]``) is *usually* a
    shadow: it carries no relative-import level, so it can never spell the
    relative ``from . import alias_name`` self-reference. This includes the
    unaliased dotted form ``import alias_name.submodule`` -- Python binds
    only the *first* dotted component (``alias_name``) in that case, not
    the full dotted path, so the bound name is derived from
    ``alias.name.split(".")[0]`` when there is no ``asname``, exactly
    mirroring Python's own dotted ``import`` binding rule. One exception:
    an *absolute* self-reference is still possible with an explicit
    ``asname`` -- ``import <path.to.base_dir>.alias_name as alias_name``
    binds the name directly to the real submodule object itself, the same
    as the absolute ``ImportFrom`` exception just below, just spelled with
    a plain ``import`` statement instead of a ``from`` import. An absolute
    ``ImportFrom`` gets
    the identical two self-reference exceptions as the relative form --
    ``from <path.to.base_dir> import alias_name [as X]`` and
    ``from <path.to.base_dir>.alias_name import anything [as X]`` -- since
    both spellings resolve to the same file on disk; an absolute *sibling*
    re-export is not chased the way a relative one is, an accepted,
    narrower gap (nothing in this repo spells an intra-package import
    absolutely)."""
    init_path = base_dir / "__init__.py"
    if not init_path.is_file():
        return False
    try:
        tree = ast.parse(_read(init_path), filename=str(init_path))
    except SyntaxError:
        return False
    submodule_aliases = _names_bound_to_submodule(tree, alias_name)
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(
            _assign_target_binds_name(t, alias_name) for t in stmt.targets
        ):
            if _value_is_submodule_alias(stmt.value, submodule_aliases):
                continue
            return True
        if (
            isinstance(stmt, ast.AnnAssign)
            and stmt.value is not None
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == alias_name
        ):
            if _value_is_submodule_alias(stmt.value, submodule_aliases):
                continue
            return True
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if stmt.name == alias_name:
                return True
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                # `import X [as alias_name]` -- a plain import can never be
                # the relative `from . import alias_name` self-reference
                # (ast.Import carries no relative-import level), so it
                # always binds alias_name to some other module -- an
                # ordinary shadow. Without an `asname`, a dotted path like
                # `import cli.helpers` binds only the first component
                # (`cli`), not the full dotted string -- Python's own
                # dotted-import binding rule.
                bound_name = alias.asname or alias.name.split(".")[0]
                if bound_name != alias_name:
                    continue
                if "." in alias.name:
                    # An *absolute* self-reference is still possible even
                    # though a relative one isn't: `import <path.to.
                    # base_dir>.alias_name as alias_name` binds alias_name
                    # directly to the real submodule object itself --
                    # mirroring the identical absolute-ImportFrom exception
                    # below, just spelled with a plain `import` statement.
                    resolved = ROOT.joinpath(*alias.name.split("."))
                    if resolved == base_dir / alias_name:
                        continue
                return True
        if isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                if (alias.asname or alias.name) != alias_name:
                    continue
                if (
                    (stmt.module or "") == ""
                    and stmt.level == 1
                    and alias.name == alias_name
                ):
                    # `from . import alias_name [as alias_name]` -- binds the
                    # name to the real submodule object itself, not a shadow.
                    continue
                if stmt.level == 1 and stmt.module == alias_name:
                    # `from .alias_name import anything [as alias_name]` --
                    # imports FROM the real submodule directly. Python must
                    # load/execute that submodule to resolve *any* name out
                    # of it, regardless of which symbol is actually pulled
                    # or what it's locally renamed to -- not a shadow.
                    continue
                if (
                    stmt.level == 1
                    and stmt.module
                    and "." not in stmt.module
                    and _sibling_reexports_submodule(
                        base_dir, stmt.module, alias.name, alias_name, 1
                    )
                ):
                    # `from .sibling import x [as alias_name]`, where
                    # sibling.py itself re-exports the real submodule under
                    # the name `x` -- transitively still the real submodule,
                    # not a shadow. See _sibling_reexports_submodule.
                    continue
                if stmt.level == 0 and stmt.module:
                    # Absolute spelling of the same two self-reference
                    # shapes already handled above for the relative form:
                    # `from <path.to.base_dir> import alias_name [as X]`
                    # (bare self-reference) or `from <path.to.base_dir>.
                    # alias_name import anything [as X]` (direct-from-
                    # submodule) both resolve to the identical real file a
                    # relative spelling would. A same-package *sibling*
                    # re-export spelled absolutely is not chased -- an
                    # accepted, narrower gap than the relative case, since
                    # nothing in this repo spells an intra-package import
                    # absolutely rather than relatively.
                    resolved = ROOT.joinpath(*stmt.module.split("."))
                    if resolved == base_dir and alias.name == alias_name:
                        continue
                    if resolved == base_dir / alias_name:
                        continue
                    # A different genuine first-party module -- not
                    # base_dir's own submodule -- can still itself be a
                    # real CLI-frontend module (`from abicheck.cli import
                    # main as cli`, or a nested adapter like
                    # `abicheck.compat.cli`): importing it already
                    # constitutes the dependency this check exists to
                    # catch, independent of whether it also happens to
                    # shadow base_dir's own same-named submodule. Treated
                    # as NOT a shadow (falls through, same as the two
                    # self-reference cases above) rather than as a real
                    # break in the shadow chain, so the caller's own
                    # on-disk existence check for base_dir's literal
                    # alias_name submodule still gates the final verdict.
                    # Only counts when the resolved path is itself a real,
                    # on-disk module -- an unresolvable/hypothetical path
                    # can't be proven CLI-touching from syntax alone.
                    mod_components = stmt.module.split(".")
                    if (
                        mod_components[0] == PKG.name
                        and any(_is_cli_component(c) for c in mod_components[1:])
                        and (resolved.with_suffix(".py").is_file() or resolved.is_dir())
                    ):
                        continue
                return True
    return False


_MAX_REEXPORT_HOPS = 5


def _sibling_reexports_submodule(
    base_dir: Path, sibling_stem: str, target: str, original_alias_name: str, depth: int
) -> bool:
    """Does ``base_dir/{sibling_stem}.py`` bind its own local name *target*
    to *base_dir*'s real ``{original_alias_name}`` submodule -- directly
    (``from . import original_alias_name [as target]``), or transitively
    through a further single-package re-export (``from .other_sibling
    import y [as target]``, chasing ``y`` the same way), up to
    ``_MAX_REEXPORT_HOPS``?

    Closes the case ``_package_shadows_attribute`` alone can't: a package
    whose ``__init__.py`` does ``from .frontend import cli``, where
    ``frontend.py`` (a plain sibling module, not the package root) is the
    one that actually does ``from . import cli`` -- importing the package
    still reaches the real CLI submodule, just one hop removed from
    ``__init__.py`` itself. *target* is the name being chased in the
    *current* module and changes at each hop (whatever the previous
    import's own source name was); *original_alias_name* stays fixed across
    the whole chase, since only an import literally spelling ``from .
    import original_alias_name`` -- at any hop, under any amount of
    intermediate renaming -- actually resolves to that specific submodule
    file. Also recognized: a hop that imports directly *from* the real
    submodule (``from .original_alias_name import anything [as target]``,
    e.g. ``from .cli import main as cli`` inside a re-export facade) --
    Python must load/execute that submodule to resolve any name out of it,
    so this reaches it too, independent of which symbol is pulled, the
    same reasoning ``_package_shadows_attribute``'s own direct-from-
    submodule exception uses. A hop that imports a *different* real
    submodule under the name being chased (``from . import othername as
    target``) correctly does not count: it shadows ``original_alias_name``
    with a different file, the same as ``_package_shadows_attribute``'s own
    Assign-based exception already distinguishes.

    *sibling_stem* may name either a plain module file (``base_dir/X.py``)
    or a subpackage (``base_dir/X/__init__.py``) -- a package facade
    re-exports the same way a module does (``frontend/__init__.py`` doing
    ``from .. import cli``), just one directory level deeper, so escaping
    back to *base_dir* from inside it needs a relative-import ``level`` of
    2, not 1. The required level is derived from which shape actually
    exists on disk (a module file always needs level 1; a subpackage always
    needs level 2), so both shapes share this one implementation instead of
    two near-duplicates.

    Deliberately bounded, not full symbol resolution: a further hop only
    ever chases another dot-free *module* sibling of *base_dir* (never a
    nested subpackage one level down from a subpackage, an absolute import,
    or a cross-package hop), and only up to ``_MAX_REEXPORT_HOPS`` deep
    (also guards against a re-export cycle, since each hop consumes one
    unit of depth regardless of the module graph's own shape) -- true to
    this checker's own false-negative-over-false-positive default, a chain
    this doesn't resolve is conservatively left unflagged rather than
    chased further."""
    if depth >= _MAX_REEXPORT_HOPS:
        return False
    sibling_file = base_dir / f"{sibling_stem}.py"
    sibling_pkg_init = base_dir / sibling_stem / "__init__.py"
    if sibling_file.is_file():
        sibling_path = sibling_file
        required_level = 1
    elif sibling_pkg_init.is_file():
        sibling_path = sibling_pkg_init
        required_level = 2
    else:
        return False
    try:
        tree = ast.parse(_read(sibling_path), filename=str(sibling_path))
    except SyntaxError:
        return False
    for stmt in tree.body:
        if not isinstance(stmt, ast.ImportFrom) or stmt.level != required_level:
            continue
        for alias in stmt.names:
            if (alias.asname or alias.name) != target:
                continue
            if (stmt.module or "") == "":
                if alias.name == original_alias_name:
                    return True
                # Imports a *different* real submodule of base_dir under
                # this local name -- not the one we're chasing.
                continue
            if stmt.module == original_alias_name:
                # `from .original_alias_name import anything [as target]`
                # -- imports directly FROM the real submodule itself, one
                # hop removed from `__init__.py`. Python must load/execute
                # that submodule to resolve *any* name out of it, so this
                # already reaches it regardless of which symbol is pulled
                # -- the same reasoning `_package_shadows_attribute`'s own
                # direct-from-submodule exception uses, mirrored here so a
                # re-export facade importing FROM the real submodule (not
                # re-exporting a name bound TO it) is recognized too.
                return True
            if stmt.module and "." not in stmt.module:
                if _sibling_reexports_submodule(
                    base_dir, stmt.module, alias.name, original_alias_name, depth + 1
                ):
                    return True
    return False


def _names_bound_to_submodule(tree: ast.Module, alias_name: str) -> set[str]:
    """Every local name in *tree* (an ``__init__.py``) bound directly to the
    real ``alias_name`` submodule object via ``from . import alias_name [as
    X]`` -- the one import shape that binds the submodule itself, as
    opposed to a name defined *inside* it."""
    names: set[str] = set()
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.ImportFrom)
            and (stmt.module or "") == ""
            and stmt.level == 1
        ):
            for alias in stmt.names:
                if alias.name == alias_name:
                    names.add(alias.asname or alias.name)
    return names


def _assign_target_binds_name(target: ast.expr, name: str) -> bool:
    """Does *target* (one element of an ``ast.Assign``'s ``targets`` list)
    bind *name* -- recursing into tuple/list destructuring (``cli, other =
    values``) and a starred target (``cli, *rest = values``)? Python
    creates an ordinary attribute for a destructured name exactly the same
    way a plain ``cli = value`` assignment does, so a same-named ``cli.py``
    submodule sitting alongside it is shadowed identically either way --
    checking only a top-level ``ast.Name`` target would miss this and
    incorrectly treat the alias as still reaching the real submodule."""
    if isinstance(target, ast.Name):
        return target.id == name
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_assign_target_binds_name(elt, name) for elt in target.elts)
    if isinstance(target, ast.Starred):
        return _assign_target_binds_name(target.value, name)
    return False


def _value_is_submodule_alias(value: ast.expr, submodule_aliases: set[str]) -> bool:
    """Is *value* a bare reference to one of *submodule_aliases* -- i.e.
    does this assignment's right-hand side name the real submodule object
    itself, rather than an unrelated value?"""
    return isinstance(value, ast.Name) and value.id in submodule_aliases


def _relative_import_base_dir(rel: str, level: int, mod: str) -> Path:
    """Directory a relative ``from`` import's *mod* (possibly empty)
    resolves to on disk, treating every engine module here as an ordinary
    module file rather than a package's own ``__init__.py`` — level 1
    resolves within the importing file's own directory, and each
    additional level walks up one more parent, mirroring Python's own
    relative-import semantics."""
    base = ROOT / Path(rel).parent
    for _ in range(level - 1):
        base = base.parent
    if mod:
        base = base.joinpath(*mod.split("."))
    return base


def _engine_boundary_violations(
    node: ast.Import | ast.ImportFrom, rel: str
) -> list[str]:
    """Return one human description per prohibited alias *node* imports
    (``click`` or a ``cli``/``cli_*`` sibling — top-level or nested, e.g.
    ``abicheck.compat.cli``), possibly more than one — an import statement
    can name several targets on one line (`import click, abicheck.cli_new`;
    `from abicheck import cli_a, cli_b`), and each prohibited one is its own
    violation; returning only the first would let a second, added alias
    ride along unflagged forever, silently absorbed into whichever
    description the allowlist already recognizes. Checks every scope
    (module- and function-level — most of these are deliberately lazy
    imports), matching this check's own "no engine module may import click
    or a CLI frontend" rule regardless of where in the file the import
    sits, and regardless of how deep the CLI module is nested (a
    ``cli_*.py`` sibling of ``cli.py``, or a ``cli.py`` living inside a
    sub-package like ``compat/``). *rel* (the importing file's own
    repo-relative path) is only used to resolve a relative import's base
    directory when verifying a trailing imported *alias* actually names a
    real submodule on disk (see ``_alias_is_real_submodule``) — a dotted
    module path never needs that check, since Python itself guarantees
    every component of one is a real module."""
    if isinstance(node, ast.Import):
        found = []
        for alias in node.names:
            parts = alias.name.split(".")
            if parts[0] == "click":
                found.append(f"import {alias.name}")
            # `import abicheck.cli_dump_helpers` / `import
            # abicheck.compat.cli` (also catches a bare `... as X`, which
            # still binds the whole dotted path). Any component after
            # `abicheck`, not just the first, since a nested CLI adapter's
            # own cli-ness can show up several segments deep. No submodule
            # check needed: every component of a dotted `import a.b.c` is
            # already, by Python's own import semantics, a real module.
            elif parts[0] == "abicheck" and any(
                _is_cli_component(p) for p in parts[1:]
            ):
                found.append(f"import {alias.name}")
        return found
    # ast.ImportFrom
    mod = node.module or ""
    mod_components = mod.split(".") if mod else []
    if node.level >= 1:
        # Relative import: `from .cli_xxx import ...`, `from .compat.cli
        # import ...`, `from . import cli_xxx`, or `from .compat import
        # cli` (a nested adapter reached via an imported alias, not a
        # dotted `mod` component).
        if any(_is_cli_component(c) for c in mod_components):
            return [f"from {'.' * node.level}{mod} import ..."]
        base_dir = _relative_import_base_dir(rel, node.level, mod)
        return [
            f"from {'.' * node.level}{mod} import {alias.name}"
            for alias in node.names
            if _is_cli_component(alias.name)
            and _alias_is_real_submodule(base_dir, alias.name)
        ]
    # Absolute import.
    if mod == "click" or mod.startswith("click."):
        return [f"from {mod} import ..."]
    if mod_components and mod_components[0] == "abicheck":
        # `from abicheck.compat.cli import main` / `from abicheck import
        # cli_dump_helpers` — a dotted component naming the CLI module, or
        # (when `mod` itself has no such component, e.g. `from
        # abicheck.compat import cli`) an imported alias naming it instead.
        # `mod_components[0]`, not just membership: `from vendor.abicheck
        # .cli import x` names an unrelated third-party `vendor.abicheck`
        # package, not this repo's own `abicheck` — matching the identical
        # `parts[0] == "abicheck"` check the `ast.Import` branch already
        # uses above.
        if any(_is_cli_component(c) for c in mod_components[1:]):
            return [f"from {mod} import ..."]
        base_dir = ROOT.joinpath(*mod_components)
        found = [
            f"from {mod} import {alias.name}"
            for alias in node.names
            if _is_cli_component(alias.name)
            and _alias_is_real_submodule(base_dir, alias.name)
        ]
        if found:
            return found
    return []


def _engine_boundary_sites(tree: ast.Module, rel: str) -> list[tuple[str, int, str]]:
    """Return one ``(allowlist_key, lineno, description)`` per boundary
    violation found in *tree* (already parsed from the file at *rel*).

    ``allowlist_key`` is the stable `"<rel>::<desc>::<occurrence>"` form
    `ENGINE_CLI_BOUNDARY_ALLOWLIST` is keyed by — see that constant's own
    comment for why occurrence-within-file rather than line number.
    ``lineno``/``description`` are kept separately so a real, unallowlisted
    violation can still be reported with its actual source location.
    """
    matches: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for desc in _engine_boundary_violations(node, rel):
            matches.append((node.lineno, desc))
    matches.sort(key=lambda m: m[0])
    occurrence: dict[str, int] = {}
    sites: list[tuple[str, int, str]] = []
    for lineno, desc in matches:
        occurrence[desc] = occurrence.get(desc, 0) + 1
        sites.append((f"{rel}::{desc}::{occurrence[desc]}", lineno, desc))
    return sites


def check_engine_cli_boundary(f: Findings) -> None:
    """ERROR if an engine-layer module (`scan_engine.py`, `service*.py`,
    `artifact_*.py`, `buildsource/**/*.py`) imports `click` or a `cli_*`
    sibling module.

    The CLI is a frontend adapter over the engine/service layer, not the
    other way around — an engine module that imports `click` or a `cli_*`
    helper has inverted that dependency, which is exactly what let
    `scan_engine.py` accumulate `click.ClickException`/`click.echo` calls
    the typed Python API path can't use either. New violations are rejected
    outright; the pre-existing ones are recorded in
    `ENGINE_CLI_BOUNDARY_ALLOWLIST` (Phase 0 of the duplication-and-
    convergence-assessment plan) rather than silently passing.
    """
    for path in sorted(PKG.rglob("*.py")):
        rel = _rel(path)
        if not _is_engine_module(rel):
            continue
        try:
            tree = ast.parse(_read(path), filename=rel)
        except SyntaxError:
            continue
        for key, lineno, desc in _engine_boundary_sites(tree, rel):
            if key in ENGINE_CLI_BOUNDARY_ALLOWLIST:
                continue
            f.err(
                "engine-cli-boundary",
                f"{rel}:{lineno}: engine module `{desc}` — engine/service/"
                "artifact/buildsource modules may not import click or a "
                "cli_* sibling (the CLI is a frontend adapter over the "
                "engine, not the reverse); see docs/contribute/plans/"
                "duplication-and-convergence-assessment.md's Phase 0/P1",
            )
