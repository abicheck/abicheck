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

"""Release *input resolution*: two release operands -> two per-library maps.

ADR-061 gap D, the "Remaining scope" note
``frontends/cli/release_compare_request.py`` used to carry: package/debug-
package/devel-package extraction, library discovery, stored-
``ProjectSnapshot`` variant materialization, ``--dso-only`` classification,
per-side header/include resolution and key matching. Every one of those
functions was ``frontends``/flat-``cli_*``-classified, and several raised
``click.UsageError``/``click.ClickException`` from inside the resolution
itself -- so the release fan-out's pre-execution half was reachable only
from a Click command, and a Python caller with no Click context received an
undocumented exception type. That is what kept
:func:`~abicheck.workflows.release_request.resolve_release_compare_plan` off
``abicheck.service``: the ``engine-cli-boundary`` gate forbids an engine
module from importing a ``cli_*`` sibling.

This module is those functions, moved, with every Click raise replaced by a
typed :class:`~abicheck.errors.ReleaseOperandContentError`/
:class:`~abicheck.errors.ReleaseOperandUsageError` -- the split preserves
the two exit codes the ``click`` types it replaced already produced (``1``
for a fact about the operand's content, ``64`` for a fact about what the
caller asked for) -- or, where one already
existed, by letting the engine's own typed error propagate -- a
:class:`~abicheck.errors.AmbiguousLibraryMatchError` from
``workflows.extraction.build_match_map``, a
:class:`~abicheck.errors.AmbiguousVariantSelectionError` from
``workflows.release_package``). Translating a typed error to a Click one is
the CLI boundary's job and stays there
(``frontends/cli/release_compare_request.py``).

Behaviour is unchanged: the messages are the same strings, and the CLI
still exits 64 with the same text for every input that produced that before
-- ``tests/test_release_request_parity.py`` states that as an equality
between a real CLI invocation and a direct
:func:`abicheck.service.resolve_release_compare` call rather than as a
claim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ..errors import ReleaseOperandContentError, ReleaseOperandUsageError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ..model.package_inventory import PackageInventory
    from .extraction import PackageExtractor

__all__ = [
    "collect_release_inputs",
    "debian_symbols_warning",
    "discover_files",
    "discover_include_roots",
    "extract_if_package",
    "match_release_keys",
    "prepare_release_inputs",
    "resolve_release_headers",
    "resolve_release_package_side",
]


def collect_release_inputs(path: Path) -> list[Path]:
    """Collect compare-able inputs from a file or directory."""
    from .extraction import is_supported_compare_input

    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ReleaseOperandContentError(
            f"Input path is neither file nor directory: {path}"
        )
    files = [p for p in sorted(path.rglob("*")) if is_supported_compare_input(p)]
    if not files:
        raise ReleaseOperandContentError(
            f"No supported ABI inputs found in directory: {path}"
        )
    return files


def resolve_release_package_side(
    side_dir: Path,
    variant_id: str | None,
    make_temp_dir: Callable[[str], Path],
    *,
    side: str,
) -> dict[str, Path] | None:
    """``None`` when *side_dir* is not a stored `ProjectSnapshot` package
    directory -- the caller falls back to its existing live-discovery path
    unchanged. Otherwise, *side_dir* is unpacked via
    `workflows.release_package.resolve_release_package_map` into
    the same canonical-key -> `Path` shape `_build_match_map` builds from a
    live directory of `.so` files (ADR-062 A1.7), so `_match_release_keys`'s
    own ``set(old_map) & set(new_map)`` matches a stored-side library
    against a live-side or another stored-side one by the identical key.

    *side* is ``"old"`` or ``"new"`` -- the `--variant` prefix naming *this*
    operand. It is used only to render an ambiguous-variant error's
    remediation example, which is why it lives here and not in the engine:
    see :class:`~abicheck.errors.AmbiguousVariantSelectionError`.
    """
    if not side_dir.is_dir():
        return None
    from .release_package import resolve_release_package_map
    from .storage import is_project_snapshot_package_dir

    if not is_project_snapshot_package_dir(side_dir):
        return None
    from ..errors import AmbiguousVariantSelectionError, SnapshotError

    dest_root = make_temp_dir("abicheck_relpkg_")
    try:
        resolved: dict[str, Path] = resolve_release_package_map(
            side_dir, variant_id=variant_id, dest_root=dest_root
        )
        return resolved
    except AmbiguousVariantSelectionError as exc:
        # `resolve_release_package_map` states the fact and carries the ids
        # but names no flag, because it does not know *which side* this
        # package is -- and a bare `--variant ID` would apply to both sides,
        # so it is not safe advice when only one side is ambiguous (Codex
        # review, PR #1184, second round). This function does know: `side` is
        # its whole reason for taking that parameter. Append the side-correct
        # example, and only when there is a real id to name: a package
        # declaring zero variants has nothing to select. (This rendering used
        # to live in the CLI layer for the same "only there is the side
        # known" reason; carrying `side` here is what let it move with the
        # resolution.)
        example = (
            f" (e.g. --variant {side}={exc.variant_ids[0]})" if exc.variant_ids else ""
        )
        raise ReleaseOperandUsageError(f"{exc}{example}") from exc
    except (KeyError, ValueError, OSError, SnapshotError) as exc:
        # Ambiguous variant, a same-key collision (ValueError), a missing/
        # unreadable ref (OSError), an object absent from objects/ entirely
        # (KeyError, DirectoryObjectStore.get's own error on a truncated
        # package; CodeRabbit review), or a corrupt document (SnapshotError)
        # are all usage errors, translated like `_build_match_map`'s own
        # `AmbiguousLibraryMatchError` (Codex review).
        raise ReleaseOperandUsageError(str(exc)) from exc


def extract_if_package(
    input_path: Path,
    debug_pkg: Path | None,
    devel_pkg: Path | None,
    make_temp_dir: Callable[[str], Path],
    is_package: Callable[[Path], bool],
    detect_extractor: Callable[[Path], PackageExtractor | None],
) -> tuple[Path, Path | None, Path | None, Path | None, bool]:
    """Extract package to tempdir if needed, return
    (lib_dir, debug_dir, header_dir, symbols_file, container_complete).

    *container_complete* (ADR-065 S3) is the primary operand's own
    :attr:`~abicheck.package.ExtractResult.container_complete`: ``True`` when
    *input_path* was a package archive this extractor unpacked in full, so a
    component absent from *lib_dir* is genuinely absent; ``False`` for a
    directory operand, which proves nothing (D2). *debug_pkg*/*devel_pkg*
    never affect it -- they carry debug info and headers, not components.

    When *input_path* is a plain directory (not a package archive), it is used
    as-is for lib_dir.  Side packages (*debug_pkg*, *devel_pkg*) are still
    extracted in that case so that standalone debug/devel packages paired with
    an already-extracted directory are not silently ignored.

    *symbols_file* (CLI-audit P2) is the Debian dpkg-gensymbols(1) contract
    file from *input_path*'s own control.tar.* when it is a .deb -- ``None``
    for every other package format and for a .deb that ships none. Only the
    primary package is consulted, not *debug_pkg*/*devel_pkg*: a `-dbg`/`-dev`
    companion package does not carry the library's own symbols contract.
    """
    # Default: treat input_path as an already-extracted library directory.
    lib_dir: Path = input_path
    debug_dir: Path | None = None
    header_dir: Path | None = None
    symbols_file: Path | None = None
    container_complete = False

    if is_package(input_path):
        extractor = detect_extractor(input_path)
        if extractor is None:
            raise ReleaseOperandContentError(
                f"Unrecognized package format: {input_path}"
            )
        target = make_temp_dir("abicheck_pkg_")
        result = extractor.extract(input_path, target)
        lib_dir = result.lib_dir
        debug_dir = result.debug_dir
        header_dir = result.header_dir
        symbols_file = result.symbols_file
        container_complete = result.container_complete

    if debug_pkg is not None:
        dbg_ext = detect_extractor(debug_pkg)
        if dbg_ext is None:
            raise ReleaseOperandContentError(
                f"Unrecognized debug package format: {debug_pkg}"
            )
        dbg_target = make_temp_dir("abicheck_dbg_")
        dbg_result = dbg_ext.extract(debug_pkg, dbg_target)
        debug_dir = dbg_result.debug_dir or dbg_result.lib_dir

    if devel_pkg is not None:
        dev_ext = detect_extractor(devel_pkg)
        if dev_ext is None:
            raise ReleaseOperandContentError(
                f"Unrecognized devel package format: {devel_pkg}"
            )
        dev_target = make_temp_dir("abicheck_dev_")
        dev_result = dev_ext.extract(devel_pkg, dev_target)
        header_dir = dev_result.header_dir or dev_result.lib_dir

    return lib_dir, debug_dir, header_dir, symbols_file, container_complete


def debian_symbols_warning(
    old_symbols_file: Path | None,
    new_symbols_file: Path | None,
) -> str | None:
    """Compare two .deb packages' dpkg-gensymbols(1) contracts, if both sides
    have one (CLI-audit P2: "Debian .symbols not integrated" -- extraction
    alone does not make the contract participate in a package compare).

    Returns a formatted diff report to fold into the release warnings list
    when the contracts disagree, else ``None`` -- purely additive/informational
    (never gates the compare's verdict/exit code): the binary ABI diff
    already gates BREAKING/API_BREAK; a symbols-contract mismatch by itself
    only means the *packaging* metadata (minimum versions, listed symbols)
    has drifted from what the binary actually exports, which is useful
    context but not on its own proof of an ABI break (ADR-028 D3's "evidence
    may add context, never silently delete/invent a break" principle,
    applied here to a cross-source packaging check the same way
    cross_source_checks.py's D4 checks apply it to build/source evidence).
    """
    if old_symbols_file is None or new_symbols_file is None:
        return None
    from ..debian_symbols import (
        diff_symbols_files,
        format_diff_report,
        load_symbols_file,
    )

    try:
        old_symbols = load_symbols_file(old_symbols_file)
        new_symbols = load_symbols_file(new_symbols_file)
    except (OSError, ValueError) as exc:
        return f"Debian symbols file could not be parsed: {exc}"
    diff = diff_symbols_files(old_symbols, new_symbols)
    if not (diff.removed or diff.added or diff.version_changed):
        return None
    return "Debian symbols contract changed:\n" + format_diff_report(diff)


def resolve_release_headers(
    headers: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_header_dir: Path | None,
    new_header_dir: Path | None,
) -> tuple[list[Path], list[Path]]:
    """Resolve each side's headers for a release comparison."""
    old_h: list[Path] = list(old_headers_only) if old_headers_only else list(headers)
    new_h: list[Path] = list(new_headers_only) if new_headers_only else list(headers)
    if old_header_dir and not old_headers_only:
        old_h = [old_header_dir]
    if new_header_dir and not new_headers_only:
        new_h = [new_header_dir]
    return old_h, new_h


def discover_include_roots(header_dir: Path | None) -> list[Path]:
    """Return common include roots from an extracted devel/header package."""
    if header_dir is None:
        return []
    candidates = [
        header_dir,
        header_dir / "usr" / "include",
        header_dir / "usr" / "local" / "include",
    ]
    usr_include = header_dir / "usr" / "include"
    if usr_include.is_dir():
        candidates.extend(p for p in usr_include.iterdir() if p.is_dir())
    seen: set[Path] = set()
    roots: list[Path] = []
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(candidate)
    return roots


def match_release_keys(
    old_dir: Path,
    new_dir: Path,
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    old_files: list[Path],
    new_files: list[Path],
    is_package: Callable[[Path], bool],
) -> tuple[list[str], dict[str, Path], dict[str, Path]]:
    """Match library keys between old and new, handling direct file pairs.

    ADR-065 S4 deleted this function's old-minus-new / new-minus-old return
    values. Pairing is all this may answer: *which* members have a
    counterpart. What an unpaired member *means* -- unmatched, expected but
    not produced, out of scope, or (only against a proven-complete inventory)
    removed or added -- is decided exclusively by
    :func:`~abicheck.workflows.release_scope.build_release_scope_record`, and
    every consumer reads that record. A second, evidence-free set difference
    here is how a name miss, a SONAME bump, a partial build and a genuine
    deletion became one state (D2, the deletion gate)."""
    direct_file_pair = (
        old_dir.is_file()
        and new_dir.is_file()
        and not is_package(old_dir)
        and not is_package(new_dir)
    )
    if direct_file_pair:
        return (
            ["__direct_pair__"],
            {"__direct_pair__": old_files[0]},
            {"__direct_pair__": new_files[0]},
        )

    return sorted(set(old_map) & set(new_map)), old_map, new_map


def discover_files(
    input_dir: Path,
    lib_dir: Path,
    include_private: bool,
    discover_shared_libraries: Callable[..., list[Path]],
    is_package: Callable[[Path], bool],
) -> list[Path]:
    """Discover library files from a directory or extracted package."""
    if is_package(input_dir):
        files = discover_shared_libraries(lib_dir, include_private=include_private)
        if not files:
            files = collect_release_inputs(lib_dir)
    else:
        files = collect_release_inputs(lib_dir)
    return files


def prepare_release_inputs(
    old_dir: Path,
    new_dir: Path,
    debug_info1: Path | None,
    debug_info2: Path | None,
    devel_pkg1: Path | None,
    devel_pkg2: Path | None,
    include_private_dso: bool,
    dso_only: bool,
    headers: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    includes: tuple[Path, ...],
    old_includes_only: tuple[Path, ...],
    new_includes_only: tuple[Path, ...],
    config_includes: tuple[Path, ...],
    extract_if_package: Callable[
        [Path, Path | None, Path | None],
        tuple[Path, Path | None, Path | None, Path | None, bool],
    ],
    discover_shared_libraries: Callable[..., list[Path]],
    is_package: Callable[[Path], bool],
    is_elf_shared_object: Callable[[Path], bool],
    *,
    old_variant: str | None = None,
    new_variant: str | None = None,
    make_temp_dir: Callable[[str], Path] | None = None,
) -> tuple[
    Path | None,
    Path | None,
    list[Path],
    list[Path],
    list[Path],
    list[Path],
    dict[str, Path],
    dict[str, Path],
    list[str],
    list[str],
    dict[str, str],
    dict[str, str],
    PackageInventory | None,
    PackageInventory | None,
]:
    """Resolve both release operands into per-library maps and matched keys.

    ADR-065 S4 removed the old-minus-new / new-minus-old key lists from this
    return: pairing answers *which* members have a counterpart and nothing
    else -- what an unpaired member means is decided once, by
    `workflows.release_scope.build_release_scope_record`. The two mappings
    before the inventories (D1) name the stored members `--dso-only` could
    not classify; the trailing two (S3) are each side's declared **component
    inventory** when that side was a package archive this run unpacked --
    `None` for a directory operand, for a stored `ProjectSnapshot` package
    (whose own `inventory_complete` assertion governs), and for a file pair.

    *old_variant*/*new_variant* and *make_temp_dir* (ADR-062 A1.7) are the
    stored-side plumbing for a `ProjectSnapshot` package operand -- ``None``
    (the default) leaves every pre-existing loose-directory/archive caller
    unaffected. When a side *is* a package directory, its variant is
    unpacked into per-library sub-package directories
    (`_resolve_release_package_side`) instead of running the live
    extraction/discovery path for that side; the two sides are resolved
    fully independently, so a `stored/live` or `live/stored` release is the
    same code path as `stored/stored`/`live/live`, just with one side's
    branch taken instead of the other's.
    """
    from ..package import package_component_inventory
    from .contract_conflicts import debian_symbols_release_conflict_lines
    from .extraction import build_match_map

    old_pkg_map = (
        resolve_release_package_side(old_dir, old_variant, make_temp_dir, side="old")
        if make_temp_dir is not None
        else None
    )
    new_pkg_map = (
        resolve_release_package_side(new_dir, new_variant, make_temp_dir, side="new")
        if make_temp_dir is not None
        else None
    )
    # ADR-065 D1/D2: a stored member --dso-only could not classify is an
    # acquisition failure the caller records, not a silent narrowing.
    old_unclassified: dict[str, str] = {}
    new_unclassified: dict[str, str] = {}
    if dso_only:
        # --dso-only's stored-side counterpart to is_elf_shared_object
        # filtering a live directory's files below (Codex review: previously
        # only applied there, so a stored non-ELF/executable artifact stayed
        # in scope). No-op on either map that's already None.
        from .release_package import dso_only_filter_pair

        old_cls, new_cls = dso_only_filter_pair(old_pkg_map, new_pkg_map)
        if old_cls is not None:
            old_pkg_map, old_unclassified = old_cls.members, old_cls.unclassified
        if new_cls is not None:
            new_pkg_map, new_unclassified = new_cls.members, new_cls.unclassified

    (old_lib_dir, old_debug_dir, old_header_dir, old_symbols_file, old_whole) = (
        extract_if_package(old_dir, debug_info1, devel_pkg1)
    )
    (new_lib_dir, new_debug_dir, new_header_dir, new_symbols_file, new_whole) = (
        extract_if_package(new_dir, debug_info2, devel_pkg2)
    )
    old_files: list[Path] = []
    new_files: list[Path] = []
    if old_pkg_map is not None:
        old_map, old_warns = dict(old_pkg_map), cast("list[str]", [])
    else:
        old_files = discover_files(
            old_dir,
            old_lib_dir,
            include_private_dso,
            discover_shared_libraries,
            is_package,
        )
        if dso_only:
            old_files = [f for f in old_files if is_elf_shared_object(f)]
        old_map, old_warns = build_match_map(old_files)
    if new_pkg_map is not None:
        new_map, new_warns = dict(new_pkg_map), cast("list[str]", [])
    else:
        new_files = discover_files(
            new_dir,
            new_lib_dir,
            include_private_dso,
            discover_shared_libraries,
            is_package,
        )
        if dso_only:
            new_files = [f for f in new_files if is_elf_shared_object(f)]
        new_map, new_warns = build_match_map(new_files)
    warning_msgs: list[str] = [
        f"Warning: {warning}" for warning in (old_warns + new_warns)
    ]
    debian_symbols_note = debian_symbols_warning(old_symbols_file, new_symbols_file)
    if debian_symbols_note is not None:
        warning_msgs.append(debian_symbols_note)
    # E-S3 case 3: each side's own declared symbols contract vs. its own
    # contained binary (distinct from the old-vs-new diff just above).
    warning_msgs.extend(
        debian_symbols_release_conflict_lines(
            old_symbols_file, old_lib_dir, new_symbols_file, new_lib_dir
        )
    )
    old_h, new_h = resolve_release_headers(
        headers,
        old_headers_only,
        new_headers_only,
        old_header_dir,
        new_header_dir,
    )
    # config_includes (the project .abicheck.yml compile.include_dirs
    # suffix, already folded into `includes` by the caller) must survive a
    # per-library-pair --old-include/--new-include override, which
    # otherwise fully replaces `includes` for that side -- so it is
    # re-appended explicitly here rather than relied on via `includes`
    # (Codex review, fresh evidence).
    old_inc = (
        list(old_includes_only) + list(config_includes)
        if old_includes_only
        else list(includes)
    )
    new_inc = (
        list(new_includes_only) + list(config_includes)
        if new_includes_only
        else list(includes)
    )
    old_inc.extend(discover_include_roots(old_header_dir))
    new_inc.extend(discover_include_roots(new_header_dir))
    matched_keys, old_map, new_map = match_release_keys(
        old_dir,
        new_dir,
        old_map,
        new_map,
        old_files,
        new_files,
        is_package,
    )
    # ADR-065 S3: the declared component inventory, built from the members
    # this run selected out of a container it unpacked *in full*. Only a
    # live package operand has one -- a stored `ProjectSnapshot` side
    # (`*_pkg_map is not None`) already carries its own `inventory_complete`
    # assertion, which `release_inventory_evidence` reads instead, and a
    # directory operand proves nothing about what the release ships.
    old_inventory = (
        package_component_inventory(old_lib_dir, old_files, container_complete=True)
        if old_pkg_map is None and old_whole
        else None
    )
    new_inventory = (
        package_component_inventory(new_lib_dir, new_files, container_complete=True)
        if new_pkg_map is None and new_whole
        else None
    )
    return (
        old_debug_dir,
        new_debug_dir,
        old_h,
        new_h,
        old_inc,
        new_inc,
        old_map,
        new_map,
        warning_msgs,
        matched_keys,
        old_unclassified,
        new_unclassified,
        old_inventory,
        new_inventory,
    )
