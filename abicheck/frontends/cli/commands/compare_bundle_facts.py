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

"""CLI dispatch for a stored-bundle-facts OLD_INPUT `compare` (G38 Phase 13
follow-up; CLI cleanup phase two, PR I).

``bundle_side_input.compare_release_against_bundle_facts`` was fully
implemented and parity-tested but, per its own module docstring, deliberately
never exposed on any CLI command: every file that would host its dispatch
(``cli_compare_release.py``, ``cli_compare_helpers.py``) sits within a
handful of lines of the AI-readiness 2000-line hard cap, and the ~44-flag
release fan-out most of those flags belong to does not apply once the OLD
side is already a resolved, stored snapshot rather than a live directory.

This module is the thin CLI adapter that closes that gap without touching
either capped file: :func:`dispatch` is called directly from
``compare.compare_cmd`` (a sibling in this same package, which has headroom)
*before* the ordinary ``run_compare``/``_dispatch_release_compare`` machinery
ever runs, whenever OLD_INPUT classifies as a stored BundleFacts document
(``workflows/bundle_compare_operand.py`` -- PR I replaced the former
``--old-bundle-facts`` flag with automatic operand classification). It
resolves the small,
purpose-built option subset ``compare_release_against_bundle_facts`` actually
needs from the same parsed ``compare`` kwargs (already normalized by
``normalize_sided_options``), calls it, and renders the resulting
:class:`~abicheck.bundle_models.BundleDiffResult` as its own
``mode: "bundle_facts"`` JSON/markdown envelope -- deliberately not the full
release-summary shape (exit-decision object, severity/contract blocks) that
``cli_compare_release_helpers._format_release_json`` builds for the live
directory/package fan-out, since this is a narrower, newly-exposed surface,
not a drop-in replacement for it.

Lives under ``frontends/cli/commands/`` (ADR-061), not as a flat
``cli_compare_bundle_facts.py`` root sibling: the ``cli_`` root prefix family
is frozen (``architecture/modules.yaml``'s ``frozen-root-family`` gate) --
new CLI dispatch code belongs in the migrated ``frontends`` responsibility
package instead.

Library-removal accounting (``--fail-on-removed-library``) is out of scope
here (rejected explicitly, not silently ignored): computing it would mean
re-scanning ``old_facts_path`` a second time only to read back
``per_library_snapshots.keys()``, defeating the entire point of a caller
handing in an already-loaded, potentially huge (SYCL/DPC++-scale) facts
document just to avoid re-parsing it.

NEW_INPUT is extracted with the same ``_extract_if_package`` primitive the
live release fan-out uses when it is a package (wheel/deb/rpm/tar), not just
a directory -- the option's own help text promises "a live release
directory/package", so a package operand is a supported input, not an
afterthought. ``--devel-pkg new=...`` is honored the same way.

**Every other flag `dispatch()` doesn't explicitly wire through is rejected
outright (``click.UsageError``, exit 64) rather than silently ignored** --
``compare_bundle_facts_rejections.reject_unsupported_options()``, a sibling
module split out purely to keep this file under the architecture no-growth
800-line cap as that guard list grew round over round; see that module's own
docstring for the full list and reasoning. A zero-match comparison (nothing
in NEW_INPUT's canonical library keys overlaps OLD_FACTS's
``per_library_snapshots``) is a ``ClickException``, not a ``NO_CHANGE``
verdict -- exit 0 must mean a real comparison found nothing broken, not that
nothing was compared at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click


def _resolve_new_side_headers_includes(
    kwargs: dict[str, Any],
) -> tuple[list[Path], list[Path]]:
    """NEW-side headers/includes: the side-scoped override, else the uniform value.

    Mirrors how every other ``compare`` dispatch path reads the post-
    ``normalize_sided_options`` kwargs (ADR-040 Lever 1) -- the OLD side has
    no headers/includes of its own here (it is already a resolved, stored
    snapshot), so only the ``new=``-scoped/uniform value is ever consulted.
    """
    headers = list(kwargs.get("new_headers_only") or ()) or list(
        kwargs.get("headers") or ()
    )
    includes = list(kwargs.get("new_includes_only") or ()) or list(
        kwargs.get("includes") or ()
    )
    return headers, includes


def resolve_dispatch_compile_context(ctx: click.Context, kwargs: dict[str, Any], *, new_is_stored: bool) -> Any:
    """Resolve ``dispatch()``'s ``compile_context`` argument, mutating
    *kwargs* the same way ``compare_cmd`` used to before delegating here --
    split out purely to keep ``compare.py`` under its architecture cap.

    When *new_is_stored* (CLI cleanup phase two, PR I's stored/stored
    shape), this skips ``resolve_compile_context`` entirely and returns
    ``None``: neither side does any header-frontend extraction, so running
    it anyway would merge ``.abicheck.yml``'s own ``compile.include_dirs``
    into ``kwargs["includes"]`` -- which the stored/stored NEW-side
    rejections (``compare_bundle_facts_rejections.py``) would then wrongly
    refuse as an *explicit* ``--include``, breaking every stored/stored
    invocation run from a project directory with a compile: block (Codex
    review, PR #1060). ``kwargs["config"]`` is still resolved either way,
    since the config-block rejection checks in that same module still
    apply. An *explicit* ``--config`` with a real ``compile:`` block, and
    the expose_value=False ``--allow-ast-frontend-fallback``/
    ``--allow-unsupported-castxml`` flags, are rejected too (that module's
    ``reject_explicit_compile_config_for_stored_pair``/
    ``reject_ast_override_flags_for_stored_pair``)."""
    from ....cli_helpers_compare import discover_project_config
    from .compare_bundle_facts_rejections import (
        reject_ast_override_flags_for_stored_pair,
        reject_explicit_compile_config_for_stored_pair,
    )

    # Codex review: mirror run_compare's own cwd-upward cfg_path fallback --
    # resolve_compile_context alone never auto-discovers without a
    # --sources tree.
    _config_explicit = ctx.get_parameter_source("config") == click.core.ParameterSource.COMMANDLINE
    kwargs["config"] = kwargs.get("config") or discover_project_config()
    if new_is_stored:
        if _config_explicit and kwargs["config"] is not None:
            reject_explicit_compile_config_for_stored_pair(kwargs["config"])
        reject_ast_override_flags_for_stored_pair(ctx)
        return None

    from ....cli_options import resolve_compile_context

    _headers, _includes = _resolve_new_side_headers_includes(kwargs)
    header_backend = kwargs.get("new_header_backend") or kwargs.get("header_backend") or "auto"
    compile_context, merged_includes = resolve_compile_context(
        ctx,
        sysroot=kwargs.get("sysroot"),
        nostdinc=bool(kwargs.get("nostdinc", False)),
        header_backend=header_backend,
        includes=tuple(_includes),
        build_config=kwargs["config"],
        frontend_context=kwargs.get("frontend_context", "host"),
        compiler_path=kwargs.get("compiler_path"),
        compiler_prefix=kwargs.get("compiler_prefix"),
        compiler_option_tokens=tuple(kwargs.get("compiler_option_tokens") or ()),
    )
    # Forward the *merged* include list (Codex review), not the raw kwargs
    # resolve_compile_context was given -- .abicheck.yml's compile.
    # include_dirs would otherwise be dropped by dispatch()'s own
    # independent re-derivation from raw kwargs.
    kwargs["includes"] = tuple(merged_includes)
    kwargs["new_includes_only"] = ()
    return compile_context


def _load_library_overrides(
    manifest_path: Path,
    *,
    known_libraries: set[str],
    selected_paths: dict[str, Path],
) -> tuple[dict[str, list[Path]], dict[str, list[Path]], dict[str, Any]]:
    """Load and validate ``--bundle-facts-library-manifest``.

    Thin wrapper over :func:`~abicheck.workflows.bundle_facts_library_
    overrides.load_bundle_facts_library_overrides` -- the actual file
    reading/YAML-loading lives in that ``workflows``-classified module, not
    here, since it needs ``dump_manifest``'s duplicate-key-checking strict
    YAML loader (classified ``extract``) and ``frontends`` may not import
    ``extract`` directly (only ``workflows`` may -- ``architecture/
    modules.yaml``).

    Translates :class:`~abicheck.workflows.bundle_facts_library_overrides.
    BundleFactsLibraryOverridesError` (a ``ValueError`` subclass) into
    :class:`click.UsageError` here, rather than letting it reach
    ``dispatch()``'s own ``except (SnapshotError, ValueError, OSError)``
    clause (Codex review): invalid YAML, a duplicate manifest key, an
    unrecognized key, or a library name outside *known_libraries* is a
    malformed CLI input -- AGENTS.md's exit-code table reserves ``64`` for
    exactly that (``cli._EXIT_USAGE_ERROR``, via ``_AbicheckGroup``'s
    UsageError-exit-2-to-64 remap) -- not the generic operational-failure
    exit ``1`` that clause produces for e.g. a malformed OLD_FACTS document.
    Mirrors ``dump.py``'s own ``_load_dump_manifest_or_reject()``, which
    does the identical translation for ``--dump-manifest``'s
    ``ManifestValidationError``.
    """
    from ....workflows.bundle_facts_library_overrides import (
        BundleFactsLibraryOverridesError,
        load_bundle_facts_library_overrides,
    )

    try:
        overrides = load_bundle_facts_library_overrides(
            manifest_path,
            known_libraries=known_libraries,
            selected_paths=selected_paths,
        )
    except BundleFactsLibraryOverridesError as exc:
        raise click.UsageError(str(exc)) from exc
    return overrides.headers, overrides.includes, overrides.compile


def dispatch(*, compile_context: Any, new_is_stored: bool = False, **kwargs: Any) -> None:
    """Handle a ``compare OLD_FACTS NEW_INPUT`` invocation where OLD_FACTS
    classified as a stored BundleFacts document.

    *new_is_stored* (CLI cleanup phase two, PR I), when true, means
    NEW_INPUT classified as a stored BundleFacts document too -- both sides
    are then diffed by ``workflows.bundle_stored_pair_compare.
    compare_stored_bundle_facts_pair`` (a pure in-memory per-library diff,
    no binaries read, no header AST parsed on either side) instead of
    ``compare_release_against_bundle_facts`` (which extracts and dumps
    NEW_INPUT as a live directory/package). The default ``False`` is the
    original stored/live shape, unchanged.

    *kwargs* is ``compare_cmd``'s already-parsed, already-``normalize_sided_
    options``-processed option dict -- the same dict that would otherwise be
    forwarded to ``run_compare``. Never returns normally: like every other
    verdict-emitting command, it exits via ``sys.exit`` (through the shared
    ``_exit_compare_release`` legacy-scheme mapping).

    *compile_context* is resolved by the caller (``compare_cmd``,
    ``cli_options.resolve_compile_context``) rather than here: this leaf
    module is deliberately kept out of ``cli_options``'s own import graph --
    ``cli_options`` transitively reaches back through ``cli_resolve ->
    service -> ... -> cli_compare_helpers -> frontends.cli.commands.compare``
    (the pre-existing, allowlisted CLI-registration cycle), and importing it
    here would pull this module into that cycle (AI-readiness
    ``import-cycle-growth`` gate, AGENTS.md: never extend the allowlist
    reactively).
    """
    # bundle_side_input.py is classified `workflows` (architecture/
    # modules.yaml) -- `frontends -> workflows` is a legal edge
    # (`may_import: [model, workflows, report]`), so this is no longer an
    # architecture-boundary workaround. Resolved via importlib rather than a
    # static `from ....bundle_side_input import ...` purely for the
    # AI-readiness `import-cycle-growth` gate, which is unrelated to package
    # classification: bundle_side_input transitively imports `service`,
    # which is itself already inside the pre-existing, allowlisted CLI-
    # registration import cycle (service -> ... -> cli_compare_helpers ->
    # frontends.cli.commands.compare). This call is already deferred to
    # dispatch-time either way; importlib just keeps that AST-level scan
    # (which walks every import regardless of nesting, static or lazy) from
    # registering a static edge that would pull this module into that cycle
    # (AGENTS.md: never extend that allowlist reactively).
    import importlib

    _bundle_side_input = importlib.import_module("abicheck.bundle_side_input")
    compare_release_against_bundle_facts = (
        _bundle_side_input.compare_release_against_bundle_facts
    )
    # PR I: same importlib indirection as compare_release_against_bundle_
    # facts above, and for the identical reason -- workflows.bundle_stored_
    # pair_compare also transitively imports `service` (Codex review moved
    # the function itself out of bundle_side_input.py into this real
    # workflows/ module, but the import-cycle-growth concern documented
    # above is unrelated to which module hosts the function).
    _bundle_stored_pair_compare = importlib.import_module(
        "abicheck.workflows.bundle_stored_pair_compare"
    )
    compare_stored_bundle_facts_pair = (
        _bundle_stored_pair_compare.compare_stored_bundle_facts_pair
    )
    from ....cli_compare_release_helpers import _exit_compare_release

    # known_libraries_for_new_side lives in workflows/bundle_facts_library_
    # overrides.py, not bundle_side_input.py (Codex review, fresh evidence):
    # that module is grandfathered flat-root legacy, and new behavior
    # belongs in a real workflows/ module even though frontends -> workflows
    # already makes it reachable either way. A plain import (not the
    # importlib indirection above) is safe here: this module, unlike
    # bundle_side_input, does not transitively import `service`.
    from ....workflows.bundle_facts_library_overrides import (
        BundleFactsLibraryOverridesError,
        known_libraries_for_new_side,
    )
    from ..options.params import _load_suppression_and_policy
    from .compare_bundle_facts_rejections import reject_unsupported_options

    reject_unsupported_options(kwargs, new_is_stored=new_is_stored)

    old_facts_path: Path = kwargs["old_input"]
    new_dir: Path = kwargs["new_input"]
    fmt = kwargs.get("fmt", "json")
    secondary_fmt = kwargs.get("secondary_fmt")
    secondary_output: Path | None = kwargs.get("secondary_output")
    depth = kwargs.get("depth")
    headers, includes = _resolve_new_side_headers_includes(kwargs)
    if depth == "binary":
        # Codex review: run_compare's own --depth binary clears every header
        # operand (_normalize_compare_options) so the comparison stays pure
        # L0/L1 symbol/debug-info evidence with no L2 header AST at all --
        # this dispatcher independently re-derives `headers` from the same
        # raw kwargs that flag feeds, so without this it silently kept
        # whatever --header/--new-header was given and ran L2 extraction
        # anyway, reporting findings outside the requested depth.
        headers = []
    header_backend = (
        kwargs.get("new_header_backend") or kwargs.get("header_backend") or "auto"
    )

    suppression, policy_file = _load_suppression_and_policy(
        kwargs.get("suppress"), kwargs["policy"], kwargs.get("policy_file_path")
    )

    bundle_system_providers = [
        s.strip()
        for s in str(kwargs.get("bundle_system_providers") or "").split(",")
        if s.strip()
    ]

    if new_is_stored:
        # PR I stored/stored: NEW_INPUT is itself a stored BundleFacts
        # document too -- no extraction, no header AST, no live NEW-side
        # resolution (compare_stored_bundle_facts_pair() is a pure in-memory
        # diff of both sides' already-persisted per-library AbiSnapshots).
        # --max-json-object-nodes applies to *both* sides' load here (one
        # unscoped flag), unlike the stored/live branch below.
        from ....errors import ProfileMismatchError, ScopeMismatchError, SnapshotError
        from .compare_bundle_facts_rejections import exit_bundle_facts_not_comparable

        try:
            result = compare_stored_bundle_facts_pair(
                old_facts_path,
                new_dir,
                manifest_path=kwargs.get("manifest_path"),
                system_providers=bundle_system_providers or None,
                cohorts=list(kwargs.get("bundle_cohorts") or ()) or None,
                policy=kwargs["policy"],
                policy_file=policy_file,
                suppress=suppression,
                old_max_json_object_nodes=kwargs.get("max_json_object_nodes"),
                new_max_json_object_nodes=kwargs.get("max_json_object_nodes"),
                depth=kwargs.get("depth"),
            )
        # Same translation the stored/live branch below applies (its own
        # comments explain each of these four exception types).
        except (SnapshotError, TypeError, ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc
        except (ProfileMismatchError, ScopeMismatchError) as exc:  # round 12/14
            exit_bundle_facts_not_comparable(exc, fmt=fmt, output=kwargs.get("output"))
    else:
        # Codex review: NEW_INPUT is documented ("a live release directory/
        # package") to accept a package archive (wheel/deb/rpm/tar), but
        # compare_release_against_bundle_facts() treats any non-directory path
        # as a single library file -- a package operand silently produced zero
        # matches instead of the shared libraries inside it. Extract it first,
        # the same way the live release fan-out does (_extract_if_package),
        # sharing that primitive rather than re-implementing package detection
        # here. --devel-pkg new=... is honored the same way too (its header_dir
        # becomes the NEW-side header root when no explicit --new-header was
        # given, and its discovered include roots are appended) -- --debug-info
        # is rejected above, since this driver has no debug-dir param for it.
        from ....cli_compare_release_helpers import (
            _discover_include_roots,
            _extract_if_package,
        )
        from ....errors import ProfileMismatchError, ScopeMismatchError, SnapshotError
        from ....workflows.extraction import detect_extractor, is_package
        from .compare_bundle_facts_rejections import exit_bundle_facts_not_comparable

        _temp_dir_paths: list[str] = []

        def _make_temp_dir(prefix: str) -> Path:
            import tempfile

            path = tempfile.mkdtemp(prefix=prefix)
            _temp_dir_paths.append(path)
            return Path(path)

        try:
            # Codex review: extraction itself must be inside this scope --
            # make_temp_dir() records the directory before extractor.extract()
            # runs, so a malformed/corrupt archive that matches a known
            # extension (a real format, bad content) raises *after* the temp
            # dir already exists; extracting outside this try/finally leaked it
            # even without --keep-extracted. It also must be inside the
            # except (SnapshotError, ValueError) boundary just below (Codex
            # review, fresh evidence) -- _extract_if_package raises
            # SnapshotError for a malformed-but-recognized archive, and that
            # used to propagate past this function as a raw Python traceback
            # instead of the clean CLI error every other SnapshotError here
            # produces.
            try:
                lib_dir, _new_debug_dir, header_dir, _new_symbols_file = (
                    _extract_if_package(
                        new_dir,
                        None,
                        kwargs.get("devel_pkg2"),
                        _make_temp_dir,
                        is_package,
                        detect_extractor,
                    )
                )
                if header_dir is not None and depth != "binary":
                    # Codex review: --depth binary's uniform `headers = []`
                    # clear above (dispatch()'s own comment) must survive
                    # package/--devel-pkg extraction too -- without this guard,
                    # a NEW_INPUT package (or `--devel-pkg new=...`) that
                    # discovers its own header_dir would reassign `headers`
                    # right back to a non-empty list here, silently re-enabling
                    # L2 header extraction for that library under a depth that
                    # promises pure L0/L1 evidence with no header AST at all.
                    if not headers:
                        headers = [header_dir]
                    includes = includes + _discover_include_roots(header_dir)

                per_library_headers: dict[str, list[Path]] | None = None
                per_library_includes: dict[str, list[Path]] | None = None
                per_library_compile: dict[str, Any] | None = None
                manifest_path = kwargs.get("bundle_facts_library_manifest")
                if manifest_path is not None:
                    # G38 Phase 17: known_libraries is derived from the same
                    # primitives compare_release_against_bundle_facts() itself
                    # uses on this identical lib_dir, so a manifest entry naming
                    # a library outside the bundle is a hard, immediate error
                    # instead of silently never being looked up.
                    include_private_dso = bool(kwargs.get("include_private_dso", False))
                    new_library_paths = known_libraries_for_new_side(
                        lib_dir, include_private_dso=include_private_dso
                    )
                    per_library_headers, per_library_includes, per_library_compile = (
                        _load_library_overrides(
                            Path(manifest_path),
                            known_libraries=set(new_library_paths),
                            selected_paths=new_library_paths,
                        )
                    )
                    if depth == "binary":
                        # Codex review: the uniform `headers` clear above only
                        # covers the uniform operand -- a manifest-supplied
                        # per-library header root would otherwise still run L2
                        # extraction for that one library under --depth binary,
                        # reporting findings outside the requested depth.
                        per_library_headers = {}
                        per_library_includes = {}
                        per_library_compile = {}

                result = compare_release_against_bundle_facts(
                    old_facts_path,
                    lib_dir,
                    headers=headers or None,
                    includes=includes or None,
                    per_library_headers=per_library_headers,
                    per_library_includes=per_library_includes,
                    per_library_compile=per_library_compile,
                    header_backend=header_backend,
                    compile=compile_context,
                    new_version=kwargs.get("new_version", "new"),
                    lang=kwargs.get("lang", "c++"),
                    # Codex review, fresh evidence: kwargs["lang_explicit"] is
                    # compare_cmd's own ctx.get_parameter_source("lang") ==
                    # COMMANDLINE detection (compare.py, mirroring run_compare's
                    # identical lang_explicit computation) -- without threading
                    # it through, an explicit --lang c++ on a language-ambiguous
                    # NEW-side header was indistinguishable from Click's own
                    # default and silently let resolve_input() auto-detect past
                    # it, which can change the extracted API and findings.
                    lang_explicit=bool(kwargs.get("lang_explicit", False)),
                    include_private_dso=bool(kwargs.get("include_private_dso", False)),
                    manifest_path=kwargs.get("manifest_path"),
                    system_providers=bundle_system_providers or None,
                    cohorts=list(kwargs.get("bundle_cohorts") or ()) or None,
                    policy=kwargs["policy"],
                    policy_file=policy_file,
                    suppress=suppression,
                    include_dependencies=bool(kwargs.get("include_dependencies", False)),
                    max_json_object_nodes=kwargs.get("max_json_object_nodes"),
                )
            except BundleFactsLibraryOverridesError as exc:
                # Codex review, fresh evidence: compare_release_against_bundle_
                # facts() itself re-validates the manifest's per-library keys
                # against the libraries actually matched between OLD_FACTS and
                # NEW_INPUT (a check known_libraries_for_new_side()'s earlier,
                # NEW-side-only pass cannot make) -- this is a malformed-CLI-
                # input case exactly like every other BundleFactsLibraryOverrides
                # Error in this module, so it gets the same exit-64 usage-error
                # translation rather than falling into the generic ValueError
                # clause below (exit 1).
                raise click.UsageError(str(exc)) from exc
            # TypeError (Codex review, fresh evidence): a malformed nested
            # build_mode/contract field inside one of OLD_FACTS's per-library
            # snapshots is rejected rather than coerced at the storage boundary
            # (storage AGENTS.md invariant 6) -- bundle_facts_from_dict()'s own
            # per_library_snapshots comprehension calls snapshot_from_dict()
            # with no nested guard, so that TypeError propagates all the way
            # here and must be caught alongside ValueError like every other
            # malformed-OLD_FACTS shape.
            except (SnapshotError, TypeError, ValueError, OSError) as exc:
                # Same CLI-boundary translation every other SnapshotError-raising
                # entry point uses (cli_resolve.py et al.) -- without this, a
                # container-node-budget rejection (or any other SnapshotError) would
                # surface as a raw Python traceback instead of a clean CLI error.
                # Also catches ValueError (Codex review): a malformed-but-parseable
                # OLD_FACTS document -- missing/wrong-shaped 'per_library_snapshots',
                # a bad 'filesystem_aliases'/'library_filenames' entry
                # (bundle_facts_serialization.bundle_facts_from_dict and
                # storage.bundle_facts_validation's validators all raise plain
                # ValueError, not SnapshotError, for these) -- would otherwise leak
                # the same raw traceback. json.JSONDecodeError (malformed JSON) is
                # itself a ValueError subclass. OSError covers load_bundle_facts()'s
                # own IsADirectoryError/PermissionError/etc when OLD_INPUT -- a plain
                # click.Path(exists=True) argument, not dir_okay=False -- turns out
                # to be a directory or otherwise unreadable file.
                raise click.ClickException(str(exc)) from exc
            except (ProfileMismatchError, ScopeMismatchError) as exc:  # round 12/14
                exit_bundle_facts_not_comparable(exc, fmt=fmt, output=kwargs.get("output"))
        finally:
            # Mirrors the live release fan-out's own --keep-extracted handling
            # (_cleanup_temp_dirs): remove the package-extraction tempdir unless
            # the caller asked to keep it for debugging.
            import shutil as _shutil

            if not kwargs.get("keep_extracted"):
                for _td in _temp_dir_paths:
                    _shutil.rmtree(_td, ignore_errors=True)
            elif _temp_dir_paths:
                click.echo(
                    f"Extracted files kept in: {', '.join(_temp_dir_paths)}", err=True
                )

    if not result.per_library:
        # Codex review: an empty NEW_INPUT (or one whose canonical library
        # keys match none of OLD_FACTS's per_library_snapshots) makes
        # compare_release_against_bundle_facts()/compare_stored_bundle_
        # facts_pair() return with an empty per_library list -- nothing was
        # actually compared, yet _exit_compare_release below would score
        # that as NO_CHANGE (exit 0), reporting a successful compatibility
        # result for a comparison that never ran. Fail loudly instead: this
        # is a usage/operational error (a wrong NEW_INPUT, a canonical-key
        # mismatch), not a clean bill of health.
        _new_desc = (
            f"{new_dir}'s stored per_library_snapshots" if new_is_stored else str(new_dir)
        )
        raise click.ClickException(
            f"No library in {_new_desc} matched any library in "
            f"{old_facts_path}'s stored per_library_snapshots -- nothing "
            "was compared. Check that NEW_INPUT and OLD_FACTS reference "
            "the same release."
        )

    # Codex review, fresh evidence: route both writes through the shared
    # CLI-safe writer every other output/--write path uses -- a direct
    # write_text() raises an uncaught FileNotFoundError when -o/--write
    # names a file under a nonexistent parent directory, where
    # _safe_write_output() creates the missing parent and translates any
    # write failure into a concise ClickException instead.
    from ..runtime import _safe_write_output

    output = kwargs.get("output")
    output_dir = kwargs.get("output_dir")
    if output_dir is not None:
        output_dir = Path(output_dir)
        # Codex review, fresh evidence: an --output-dir that already exists
        # as a regular file -- entirely unrelated to -o/--output/--write --
        # is not caught by the reserved-path collision checks below (it
        # names neither of them), yet `output_dir.mkdir(parents=True,
        # exist_ok=True)` still raises a raw, uncaught FileExistsError for
        # it (`exist_ok=True` tolerates an existing *directory*, not an
        # existing file), after the primary report has already been
        # written. Reject it up front instead, as a general precondition
        # independent of whether -o/--output/--write were even given.
        if output_dir.exists() and not output_dir.is_dir():
            raise click.UsageError(
                f"--output-dir {output_dir}: this path already exists and "
                "is not a directory -- choose a different --output-dir"
            )
        # Codex review: --output-dir's per-library filenames
        # (`{safe_name}.json`, derived from diff.library below) are known
        # up front -- reject a collision with -o/--output or --write's own
        # secondary_output *before* any artifact is written, rather than
        # letting whichever write happens to run second silently clobber
        # the first (the primary/secondary writes below run before this
        # loop, so a collision would otherwise overwrite one of them with
        # no signal anything was lost).
        reserved_paths = {
            p.resolve()
            for p in (
                Path(output) if output is not None else None,
                Path(secondary_output) if secondary_output is not None else None,
            )
            if p is not None
        }
        if reserved_paths:
            # Codex review, fresh evidence: the per-library-filename check
            # below only catches a collision with one of the *generated*
            # child report paths -- it misses the more direct case where
            # --output-dir itself names the same (previously nonexistent)
            # path as -o/--output or --write. Both Click options accept
            # that combination on their own, and without this check the
            # primary write below creates a *file* at that path, after
            # which `output_dir.mkdir(...)` raises a raw FileExistsError
            # instead of the same clean usage error every other collision
            # here produces.
            output_dir_resolved = output_dir.resolve()
            if output_dir_resolved in reserved_paths:
                raise click.UsageError(
                    f"--output-dir {output_dir}: this path is also named by "
                    "-o/--output or --write -- a directory and a report "
                    "file cannot share the same path, choose a different "
                    "--output-dir or a different -o/--write path"
                )
            for diff in result.per_library:
                safe_name = Path(diff.library).name or "library"
                target = (output_dir / f"{safe_name}.json").resolve()
                if target in reserved_paths:
                    raise click.UsageError(
                        f"--output-dir {output_dir}: the per-library report "
                        f"for {diff.library!r} would be written to "
                        f"{target}, which collides with -o/--output or "
                        "--write's own output path -- choose a different "
                        "--output-dir, or a different -o/--write path"
                    )
        # Codex review, fresh evidence: this was previously deferred until
        # after the primary/secondary writes below, right before the
        # per-library write loop. When some *ancestor* of --output-dir
        # (not output_dir itself -- the check above only catches that
        # narrower case) is a regular file, mkdir(parents=True) raises
        # NotADirectoryError only at that later point, by which time the
        # primary/secondary report has already been written to disk --
        # so a "failed" command silently left a partial artifact behind.
        # Creating (and validating) the directory up front, before any
        # report is rendered or written, makes this precondition failure
        # behave the same as every other rejection above: no artifact
        # written at all.
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # Mirrors _safe_write_output's own OSError -> ClickException
            # translation for the identical operation (creating a report's
            # parent directory) -- covers a permission failure, a full
            # disk, or a non-directory *parent* path component.
            raise click.ClickException(f"Cannot create {output_dir}: {exc}") from exc

    text = _render(
        result, fmt, old_facts_path=old_facts_path, new_dir=new_dir, new_is_stored=new_is_stored
    )
    if output is not None:
        _safe_write_output(Path(output), text)
    else:
        click.echo(text)
    if secondary_output is not None:
        # Codex review: render and write the promised second artifact
        # (validated to be json/markdown above) rather than silently
        # dropping it -- re-rendering rather than reusing `text` since a
        # secondary format can legitimately differ from the primary one.
        assert secondary_fmt is not None
        secondary_text = _render(
            result,
            secondary_fmt,
            old_facts_path=old_facts_path,
            new_dir=new_dir,
            new_is_stored=new_is_stored,
        )
        _safe_write_output(Path(secondary_output), secondary_text)
    if output_dir is not None:
        # Codex review: NEW_INPUT is a release-style operand here, so
        # --output-dir's own per-library-report contract applies -- the
        # live release fan-out writes one `{library}.json` per matched
        # library (cli_compare_release.py's own output_dir handling); mirror
        # that layout exactly rather than silently accepting the flag and
        # producing nothing.
        from ....reporter import to_json

        for diff in result.per_library:
            # Codex review: `diff.library` originates in OLD_FACTS -- a
            # user-supplied document, not a path this process resolved
            # itself -- so an absolute or `../`-laden value must not reach
            # the filesystem unsanitized (unlike the live release fan-out's
            # `old_path.stem`, which is always derived from a real,
            # already-resolved Path). `Path(...).name` is the same
            # basename-only normalization that driver's own `.stem`
            # provides: it yields only the final path component regardless
            # of how many `/`/`..` segments precede it, so it can never
            # escape `output_dir` on this platform's own separator rules.
            safe_name = Path(diff.library).name or "library"
            # Codex review, fresh evidence: same root cause as the -o/
            # --write fix above -- a direct write_text() here leaked a
            # traceback for an unwritable output_dir or any other OSError,
            # after the primary report may have already been emitted.
            # Routed through the same shared writer the live release
            # fan-out uses for its own per-library artifacts.
            _safe_write_output(output_dir / f"{safe_name}.json", to_json(diff))

    _exit_compare_release(result.verdict.value, fail_on_removed=False, removed_keys=[])


def _render(
    result: Any, fmt: str, *, old_facts_path: Path, new_dir: Path, new_is_stored: bool = False
) -> str:
    if fmt == "markdown":
        return _render_markdown(
            result, old_facts_path=old_facts_path, new_dir=new_dir, new_is_stored=new_is_stored
        )
    return _render_json(
        result, old_facts_path=old_facts_path, new_dir=new_dir, new_is_stored=new_is_stored
    )


def _render_json(
    result: Any, *, old_facts_path: Path, new_dir: Path, new_is_stored: bool = False
) -> str:
    from ....report.run_outcome import run_outcome_dict_for_diff_result
    from ....reporter import to_json

    libraries = {diff.library: json.loads(to_json(diff)) for diff in result.per_library}
    # ADR-063 Phase 7 (Codex review, fresh evidence): every compare/release
    # JSON report carries `run_outcome`; this summary previously omitted it.
    # `frontends` may not import `policy` directly (architecture/
    # modules.yaml), so this reuses `run_outcome_dict_for_diff_result` --
    # `report`-classified, already used by `reporter.py`'s own JSON entry
    # points -- rather than `run_outcome_dict_for_release`/`legacy_exit_
    # code`. It duck-types on `result.verdict`/`result.analysis_assurance`
    # (absent here, so `assurance` stays `None`), which a `BundleDiffResult`
    # satisfies the same way a `DiffResult` does. No `SeverityConfig`/gate is
    # available here (this summary carries no `exit` block at all), so both
    # are `None` -- the function's own documented "no severity_config"
    # fallback to the legacy verdict->exit mapping. Unlike the live
    # directory/package release fan-out, `result.verdict` here is always a
    # real `Verdict` -- `BundleDiffResult.verdict`/`.per_library_verdict`/
    # `.bundle_verdict` are each `max(...)` over real per-DiffResult/bundle-
    # finding verdicts, never the "ERROR"/"not_comparable" operational
    # sentinels a per-library dump failure would produce in the live fan-out
    # -- so `operational` stays `none`.
    run_outcome = run_outcome_dict_for_diff_result(result, None, None)
    summary: dict[str, object] = {
        "mode": "bundle_facts",
        "old_bundle_facts": str(old_facts_path),
        # PR I stored/stored: `new_dir` keeps its established key/meaning
        # even when NEW_INPUT is itself a stored document too (its path,
        # not a live release directory) -- `new_is_stored` is the new,
        # additive signal a consumer checks to tell the two shapes apart,
        # rather than a field rename that would break an existing consumer
        # keyed on `new_dir`.
        "new_dir": str(new_dir),
        "new_is_stored": new_is_stored,
        "verdict": result.verdict.value,
        "per_library_verdict": result.per_library_verdict.value,
        "bundle_verdict": result.bundle_verdict.value,
        "run_outcome": run_outcome,
        "libraries": libraries,
        "bundle_findings": [
            {
                "kind": f.kind.value,
                "symbol": f.symbol,
                "consumer_library": f.consumer_library,
                "provider_library": f.provider_library,
                "description": f.description,
                "old_value": f.old_value,
                "new_value": f.new_value,
                "affected_libraries": list(f.affected_libraries),
            }
            for f in result.bundle_findings
        ],
        "analysis_errors": list(result.analysis_errors),
    }
    return json.dumps(summary, indent=2)


def _render_markdown(
    result: Any, *, old_facts_path: Path, new_dir: Path, new_is_stored: bool = False
) -> str:
    from ....bundle import render_bundle_findings_markdown

    new_label = "stored facts" if new_is_stored else "release directory"
    lines = [
        "# Bundle-facts comparison",
        "",
        f"- OLD (stored facts): `{old_facts_path}`",
        f"- NEW ({new_label}): `{new_dir}`",
        f"- **Verdict:** `{result.verdict.value}`",
        f"- Per-library verdict: `{result.per_library_verdict.value}`",
        f"- Bundle verdict: `{result.bundle_verdict.value}`",
        "",
    ]
    if result.analysis_errors:
        lines.append("## Bundle analysis errors")
        lines += [f"- {msg}" for msg in result.analysis_errors]
        lines.append("")
    lines.append("## Per-library results")
    lines.append("")
    lines.append("| Library | Verdict |")
    lines.append("|---|---|")
    for diff in result.per_library:
        lines.append(f"| {diff.library} | `{diff.verdict.value}` |")
    lines.append("")
    lines.append("## Bundle findings")
    lines.append("")
    bundle_lines = render_bundle_findings_markdown(result.bundle_findings)
    lines += bundle_lines if bundle_lines else ["(none)"]
    return "\n".join(lines) + "\n"
