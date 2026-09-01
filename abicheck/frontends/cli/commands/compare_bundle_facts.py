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

"""CLI dispatch for ``compare --old-bundle-facts`` (G38 Phase 13 follow-up).

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
ever runs, whenever ``--old-bundle-facts`` is set. It resolves the small,
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


def dispatch(*, compile_context: Any, **kwargs: Any) -> None:
    """Handle a ``compare OLD_FACTS NEW_DIR --old-bundle-facts`` invocation.

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

    compare_release_against_bundle_facts = importlib.import_module(
        "abicheck.bundle_side_input"
    ).compare_release_against_bundle_facts
    from ....cli_compare_release_helpers import _exit_compare_release
    from ..options.params import _load_suppression_and_policy
    from .compare_bundle_facts_rejections import reject_unsupported_options

    reject_unsupported_options(kwargs)

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
    # is rejected above rather than silently dropped, since this driver has
    # no debug-dir parameter to forward it to.
    from ....cli_compare_release_helpers import (
        _discover_include_roots,
        _extract_if_package,
    )
    from ....errors import SnapshotError
    from ....workflows.extraction import detect_extractor, is_package

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
            if header_dir is not None:
                if not headers:
                    headers = [header_dir]
                includes = includes + _discover_include_roots(header_dir)

            result = compare_release_against_bundle_facts(
                old_facts_path,
                lib_dir,
                headers=headers or None,
                includes=includes or None,
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
        except (SnapshotError, ValueError, OSError) as exc:
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
            # the same raw traceback. json.JSONDecodeError (genuinely malformed
            # JSON, from the plain-JSON load path) is itself a ValueError
            # subclass, so it's covered by the same clause. OSError (Codex
            # review, fresh evidence) covers load_bundle_facts()'s own
            # IsADirectoryError/PermissionError/etc when OLD_INPUT -- a plain
            # click.Path(exists=True) argument, not dir_okay=False, since the
            # ordinary live-directory compare mode needs a directory there --
            # turns out to be a directory or otherwise unreadable file.
            raise click.ClickException(str(exc)) from exc
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
        # compare_release_against_bundle_facts() return with an empty
        # per_library list -- nothing was actually compared, yet
        # _exit_compare_release below would score that as NO_CHANGE (exit
        # 0), reporting a successful compatibility result for a comparison
        # that never ran. Fail loudly instead: this is a usage/operational
        # error (a wrong NEW_INPUT, a canonical-key mismatch), not a clean
        # bill of health.
        raise click.ClickException(
            f"No library in {new_dir} matched any library in "
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

    text = _render(result, fmt, old_facts_path=old_facts_path, new_dir=new_dir)
    output = kwargs.get("output")
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
            result, secondary_fmt, old_facts_path=old_facts_path, new_dir=new_dir
        )
        _safe_write_output(Path(secondary_output), secondary_text)
    output_dir = kwargs.get("output_dir")
    if output_dir is not None:
        # Codex review: NEW_INPUT is a release-style operand here, so
        # --output-dir's own per-library-report contract applies -- the
        # live release fan-out writes one `{library}.json` per matched
        # library (cli_compare_release.py's own output_dir handling); mirror
        # that layout exactly rather than silently accepting the flag and
        # producing nothing.
        from ....reporter import to_json

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
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


def _render(result: Any, fmt: str, *, old_facts_path: Path, new_dir: Path) -> str:
    if fmt == "markdown":
        return _render_markdown(result, old_facts_path=old_facts_path, new_dir=new_dir)
    return _render_json(result, old_facts_path=old_facts_path, new_dir=new_dir)


def _render_json(result: Any, *, old_facts_path: Path, new_dir: Path) -> str:
    from ....reporter import to_json

    libraries = {diff.library: json.loads(to_json(diff)) for diff in result.per_library}
    summary: dict[str, object] = {
        "mode": "bundle_facts",
        "old_bundle_facts": str(old_facts_path),
        "new_dir": str(new_dir),
        "verdict": result.verdict.value,
        "per_library_verdict": result.per_library_verdict.value,
        "bundle_verdict": result.bundle_verdict.value,
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


def _render_markdown(result: Any, *, old_facts_path: Path, new_dir: Path) -> str:
    from ....bundle import render_bundle_findings_markdown

    lines = [
        "# Bundle-facts comparison",
        "",
        f"- OLD (stored facts): `{old_facts_path}`",
        f"- NEW (release directory): `{new_dir}`",
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
