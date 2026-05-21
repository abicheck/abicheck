# Copyright 2026 Nikolay Petrov
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

"""CLI — baseline registry command group (ADR-022).

Split out of :mod:`abicheck.cli` to keep that module under the
Ai-readiness file-size limit. Imported for side-effect at the bottom
of :mod:`abicheck.cli` so the ``@main.group("baseline")`` decorator
runs.
"""
from __future__ import annotations

import json
from pathlib import Path

import click

from .cli import _safe_write_output, _setup_verbosity, main
from .errors import AbicheckError
from .serialization import snapshot_to_json

# ---------------------------------------------------------------------------
# Baseline registry commands (ADR-022)
# ---------------------------------------------------------------------------

@main.group("baseline")
def baseline_group() -> None:
    """Manage ABI baseline snapshots.

    Push, pull, list, and delete baseline snapshots from a registry.
    Default backend: filesystem (--registry file:///path/to/baselines).
    """


@baseline_group.command("push")
@click.argument("library", type=str)
@click.option("--version", "version", required=True,
              help="Version or branch name for the baseline.")
@click.option("--platform", "platform", required=False,
              help="Target platform (e.g. 'linux-x86_64'). Use --auto-platform to detect.")
@click.option("--variant", default="",
              help="Build variant (e.g. 'debug', 'ssl-enabled').")
@click.option("--snapshot", "snapshot_path", required=True,
              type=click.Path(exists=True, path_type=Path),
              help="Path to the ABI snapshot JSON file.")
@click.option("--registry", "registry_path", type=click.Path(path_type=Path),
              default=None,
              help="Path to the baseline registry directory. "
                   "Defaults to .abicheck/baselines in the current directory.")
@click.option("--auto-platform", is_flag=True, default=False,
              help="Auto-detect platform from the binary in the snapshot.")
@click.option("--git-commit", default=None,
              help="Source commit SHA to record in baseline metadata.")
@click.option("-v", "--verbose", is_flag=True, default=False)
def baseline_push(
    library: str, version: str, platform: str, variant: str,
    snapshot_path: Path, registry_path: Path | None,
    auto_platform: bool, git_commit: str | None, verbose: bool,
) -> None:
    """Push an ABI baseline snapshot to the registry.

    \b
    Example:
      abicheck baseline push libfoo --version 1.0.0 --platform linux-x86_64 \\
        --snapshot build/abi-snapshot.json
    """
    _setup_verbosity(verbose)
    from .baseline import BaselineKey, BaselineMetadata, FilesystemRegistry
    from .serialization import load_snapshot as _load

    reg_path = registry_path or Path(".abicheck/baselines")
    registry = FilesystemRegistry(reg_path)

    from .serialization import snapshot_to_json

    snapshot = _load(snapshot_path)
    # Compute checksum from canonical serialization (same form registry.push stores)
    canonical_json = snapshot_to_json(snapshot)
    meta = BaselineMetadata.create(canonical_json, git_commit=git_commit)

    effective_platform: str | None = platform
    if auto_platform and not platform:
        # Detect platform from the library path embedded in the snapshot
        if snapshot.library:
            lib_path = Path(snapshot.library)
            if lib_path.exists():
                from .baseline import detect_platform_from_binary
                effective_platform = detect_platform_from_binary(lib_path)
                if effective_platform is None:
                    raise click.UsageError(
                        "--auto-platform: failed to detect binary architecture. "
                        "Use --platform to specify the platform explicitly."
                    )
                click.echo(f"Auto-detected platform: {effective_platform}", err=True)
            else:
                raise click.UsageError(
                    f"--auto-platform: binary '{snapshot.library}' not found on disk. "
                    "Use --platform to specify the platform explicitly."
                )
        else:
            raise click.UsageError(
                "--auto-platform: snapshot has no library path. "
                "Use --platform to specify the platform explicitly."
            )

    if effective_platform is None:
        raise click.UsageError(
            "Platform is required. Use --platform or --auto-platform with a detectable binary."
        )

    try:
        key = BaselineKey(library=library, version=version, platform=effective_platform, variant=variant)
    except (ValueError, AbicheckError) as exc:
        raise click.ClickException(str(exc)) from exc
    ref = registry.push(key, snapshot, meta)
    click.echo(f"Baseline pushed: {ref}", err=True)


@baseline_group.command("pull")
@click.argument("spec", type=str)
@click.option("-o", "--output", type=click.Path(path_type=Path), required=True,
              help="Output path for the snapshot JSON file.")
@click.option("--registry", "registry_path", type=click.Path(path_type=Path),
              default=None,
              help="Path to the baseline registry directory.")
@click.option("-v", "--verbose", is_flag=True, default=False)
def baseline_pull(spec: str, output: Path, registry_path: Path | None, verbose: bool) -> None:
    """Pull an ABI baseline snapshot from the registry.

    SPEC is a colon-separated key: library:version:platform[:variant]

    \b
    Example:
      abicheck baseline pull libfoo:1.0.0:linux-x86_64 -o baseline.json
    """
    _setup_verbosity(verbose)
    from .baseline import BaselineKey, FilesystemRegistry

    reg_path = registry_path or Path(".abicheck/baselines")
    registry = FilesystemRegistry(reg_path)

    try:
        key = BaselineKey.from_spec(spec)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    result = registry.pull(key)
    if result is None:
        raise click.ClickException(f"Baseline not found: {key.path}")

    snapshot, meta = result
    snap_json = snapshot_to_json(snapshot)
    _safe_write_output(output, snap_json)
    click.echo(
        f"Baseline pulled: {key.path} (abicheck {meta.abicheck_version}, "
        f"created {meta.created_at})",
        err=True,
    )


@baseline_group.command("list")
@click.argument("prefix", required=False, default=None)
@click.option("--registry", "registry_path", type=click.Path(path_type=Path),
              default=None,
              help="Path to the baseline registry directory.")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]),
              default="text", show_default=True)
@click.option("-v", "--verbose", is_flag=True, default=False)
def baseline_list(prefix: str | None, registry_path: Path | None, fmt: str, verbose: bool) -> None:
    """List available ABI baselines in the registry.

    \b
    Example:
      abicheck baseline list
      abicheck baseline list libfoo
      abicheck baseline list --format json
    """
    _setup_verbosity(verbose)
    from .baseline import FilesystemRegistry

    reg_path = registry_path or Path(".abicheck/baselines")
    registry = FilesystemRegistry(reg_path)

    keys = registry.list(prefix=prefix)
    if not keys:
        click.echo("No baselines found.", err=True)
        return

    if fmt == "json":
        click.echo(json.dumps([
            {"library": k.library, "version": k.version,
             "platform": k.platform, "variant": k.variant, "path": k.path}
            for k in keys
        ], indent=2))
    else:
        for k in keys:
            click.echo(k.path)


@baseline_group.command("delete")
@click.argument("spec", type=str)
@click.option("--registry", "registry_path", type=click.Path(path_type=Path),
              default=None,
              help="Path to the baseline registry directory.")
@click.option("-v", "--verbose", is_flag=True, default=False)
def baseline_delete(spec: str, registry_path: Path | None, verbose: bool) -> None:
    """Delete an ABI baseline from the registry.

    SPEC is a colon-separated key: library:version:platform[:variant]

    \b
    Example:
      abicheck baseline delete libfoo:0.9.0:linux-x86_64
    """
    _setup_verbosity(verbose)
    from .baseline import BaselineKey, FilesystemRegistry

    reg_path = registry_path or Path(".abicheck/baselines")
    registry = FilesystemRegistry(reg_path)

    try:
        key = BaselineKey.from_spec(spec)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if registry.delete(key):
        click.echo(f"Baseline deleted: {key.path}", err=True)
    else:
        raise click.ClickException(f"Baseline not found: {key.path}")

