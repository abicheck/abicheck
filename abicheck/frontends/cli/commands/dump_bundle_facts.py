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

"""``dump --bundle-facts-out``: the CLI adapter of the release capture.

Validates the invocation, resolves the project-config inputs ``dump``
already resolved into the typed :class:`~abicheck.workflows.
release_bundle_facts_dump.ReleaseBundleFactsDumpRequest`, runs it, and
translates the release resolution's typed errors the way the two-sided
release boundary does (a usage fact exits 64, a content fact exits 1).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import click

if TYPE_CHECKING:
    from ....compile_context import CompileContext

F = TypeVar("F", bound=Callable[..., Any])


def dump_bundle_facts_out_option(func: F) -> F:
    """``dump --bundle-facts-out PATH`` (the spelling ``compare`` uses for
    the same document) and its ``--variant ID`` selector."""
    func = click.option(
        "--variant",
        "variant",
        default=None,
        metavar="ID",
        help="With --bundle-facts-out: which build variant to capture when "
        "SO_PATH is a stored ProjectSnapshot package declaring more than one "
        "(compare's --variant, unsided: dump has one operand). Defaults to "
        "the package's only variant; a usage error when it declares several.",
    )(func)
    return click.option(
        "--bundle-facts-out",
        "bundle_facts_out",
        type=click.Path(path_type=Path),
        default=None,
        help="Capture a directory or package release (SO_PATH) as a "
        "BundleFacts document at PATH: every library's snapshot, the "
        "release's public surface from -H, and its instantiation manifest, "
        "if any. The document a later `compare BUNDLE_FACTS NEW_RELEASE` "
        "reads as its stored OLD side, and the same one `compare "
        "--bundle-facts-out` writes for its OLD side. Replaces -o/--output.",
    )(func)


#: The ``dump`` inputs a release capture does not apply, by the spelling a
#: user typed. Refused rather than silently dropped.
_NOT_APPLIED = (
    ("output", "-o/--output"),
    ("dry_run", "--dry-run"),
    ("dump_manifest_path", "--dump-manifest"),
    ("sources", "--sources"),
    ("build_info", "--build-info"),
    ("debug_roots", "--debug-info"),
    ("provenance", "--provenance"),
    ("follow_deps", "--follow-deps / deps.follow"),
)


def _reject_unapplied_inputs(stated: Mapping[str, object]) -> None:
    """Usage error naming every *stated* input a release capture ignores."""
    named = [flag for key, flag in _NOT_APPLIED if stated.get(key)]
    if named:
        raise click.UsageError(
            f"--bundle-facts-out does not apply {', '.join(named)}: it "
            "captures every library of a release with -H/--include/"
            "--exclude-header/--label and the project config's compile "
            "settings. Drop the listed inputs, or dump one library without "
            "--bundle-facts-out."
        )


def run_dump_bundle_facts(
    so_path: Path | None,
    bundle_facts_out: Path | None,
    *,
    variant: str | None = None,
    unapplied: Mapping[str, object],
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    exclude_headers: tuple[str, ...],
    version: str,
    lang: str,
    lang_explicit: bool,
    depth: str | None,
    compile_context: CompileContext,
    include_dependencies: bool,
    config_path: Path | None,
) -> None:
    """Run ``dump SO_PATH --bundle-facts-out PATH`` and report what it wrote.

    *unapplied* holds the ``dump`` inputs a capture does not apply, keyed as
    in :data:`_NOT_APPLIED`; any one stated is a usage error.
    """
    from ....cli_resolve import classify_compare_operand
    from ....errors import (
        AmbiguousLibraryMatchError,
        ReleaseOperandContentError,
        ReleaseOperandUsageError,
        SnapshotError,
    )
    from ....workflows.release_bundle_facts_dump import (
        ReleaseBundleFactsDumpRequest,
        dump_release_bundle_facts,
    )

    if bundle_facts_out is None:
        raise click.UsageError(
            "--variant selects the build variant a --bundle-facts-out capture "
            "reads; it does nothing without --bundle-facts-out."
        )
    _reject_unapplied_inputs(unapplied)
    if so_path is None or classify_compare_operand(so_path) not in (
        "directory",
        "package",
    ):
        raise click.UsageError(
            "--bundle-facts-out captures a directory or package release; "
            f"{so_path or 'no SO_PATH'} is not one. Dump a single library "
            "without --bundle-facts-out."
        )
    request = ReleaseBundleFactsDumpRequest(
        operand=so_path,
        output=bundle_facts_out,
        headers=headers,
        includes=includes,
        version=version,
        lang=lang,
        lang_explicit=lang_explicit,
        depth=depth,
        compile_context=compile_context,
        include_dependencies=include_dependencies,
        public_header_dirs=_config_public_header_dirs(config_path),
        exclude_headers=exclude_headers,
        variant=variant,
    )
    try:
        result = dump_release_bundle_facts(
            request, notice=lambda text: click.echo(text, err=True)
        )
    except ReleaseOperandUsageError as exc:
        raise click.UsageError(_dump_variant_message(exc)) from exc
    except (
        ReleaseOperandContentError,
        AmbiguousLibraryMatchError,
        SnapshotError,
    ) as exc:
        raise click.ClickException(str(exc)) from exc
    degraded = f", {len(result.degraded)} degraded" if result.degraded else ""
    click.echo(
        f"BundleFacts written to {bundle_facts_out} "
        f"({len(result.members)} libraries{degraded})",
        err=True,
    )
    for key, reason in sorted(result.degraded.items()):
        click.echo(f"  degraded: {key}: {reason}", err=True)


def _config_public_header_dirs(config_path: Path | None) -> list[Path]:
    """``scope.public_header_dirs`` of the project config ``dump`` resolved,
    the list a directory ``compare`` hands its member dumps.

    A malformed config is a usage error, discovered or named, as it is for
    ``compare``: the capture must not silently drop the config's public-header
    scope that ``compare --bundle-facts-out`` would refuse to run without."""
    if config_path is None:
        return []
    from ....workflows.extraction import load_build_config
    from ....workflows.public_header_boundary import (
        project_config_public_header_dirs,
    )

    try:
        cfg = load_build_config(config_path)
    except ValueError as exc:
        raise click.UsageError(
            f"cannot parse build config {config_path}: {exc}"
        ) from exc
    return project_config_public_header_dirs(cfg)


def _dump_variant_message(exc: Exception) -> str:
    """The resolution's message, with the variant example in ``dump``'s own
    unsided spelling: the engine names no flag for an ambiguous package and
    the two-sided release boundary appends ``--variant old=ID``, which
    ``dump`` does not take."""
    from ....errors import AmbiguousVariantSelectionError

    cause = exc.__cause__
    if isinstance(cause, AmbiguousVariantSelectionError):
        ids = list(cause.variant_ids)
        example = f" (e.g. --variant {ids[0]})" if ids else ""
        return f"{cause}{example}"
    return str(exc)
