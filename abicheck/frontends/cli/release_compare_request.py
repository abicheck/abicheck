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

"""``ReleaseCompareRequest``/``ReleaseComparePlan`` -- ADR-061 gap D,
closure package 4's next slice: the release fan-out's own *pre-execution*
resolution (input discovery, ADR-065 scope/inventory evidence, the
``ReleaseScopePlan``, the resolved ``GateOptions``, and each side's
degraded-member markers) as one typed request/plan pair, constructible and
resolvable with a single ordinary function call -- not only reachable by
running the ``compare-release`` Click command.

**What this closes.** Before this module existed, ``cli_compare_release.
compare_release_cmd`` assembled this same information as roughly a dozen
independent local variables threaded by hand through four separate calls
(``_prepare_compare_release_inputs``, ``release_inventory_evidence``,
``resolve_release_scope_plan``, ``resolve_release_gate_options``, plus
``stored_side_inventory_complete``/``stored_degraded_members``) — exactly
the "selection, inventory, and acquisition state reach the engine as CLI
parameters" gap ADR-061's gap D and
``docs/contribute/plans/vision-api-abi-evolution.md`` both name. Those
locals are now fields on :class:`ReleaseCompareRequest` (the caller's
inputs) and :class:`ReleaseComparePlan` (the resolved outcome), and
:func:`resolve_release_compare_plan` is the one function that turns one
into the other — callable directly from Python with no Click context, no
``click.Context``, and no CLI invocation at all.
:class:`TestReleaseCompareRequestParity` (``tests/
test_release_compare_request.py``) is the completion test: a CLI-shaped
invocation of ``compare-release`` and a direct, typed-request-shaped call
to :func:`resolve_release_compare_plan` -- given the same operands and
options -- resolve to the *same* scope, gate configuration, and degraded-
member markers.

**What this does not yet close.** ``resolve_release_compare_plan`` is
"reachable from Python without Click" in the sense that matters for the
completion test above (it is one plain function call, and every unit test
in this module's own test file constructs a request and calls it directly)
-- but it is not yet reachable from ``abicheck.service``'s typed Python API
surface, because the functions it necessarily calls
(``_prepare_compare_release_inputs``, ``_resolve_release_package_side``,
and siblings) are still classified ``frontends``/flat ``cli_*``, not
``workflows`` -- the ``engine-cli-boundary`` AI-readiness gate forbids
``abicheck.service`` (an engine-adjacent module under that gate) from
importing them, and at least one of them
(``frontends.cli.release_variant_operand._resolve_release_package_side``)
still raises a real ``click.UsageError`` on a malformed stored-package
variant, which a caller with no Click context receives as a plain,
uncaught exception rather than a documented typed one. Relocating that
whole call chain into ``workflows``/``extract`` (and converting its
Click-exception raises to the typed ``errors.py`` hierarchy the rest of
the engine uses) is real, separately-scoped follow-up work -- see the
"Remaining scope" note in ADR-061's own gap D section, added alongside
this module.

Deliberately placed under ``frontends/cli/`` (not ``workflows/``) for
exactly the reason above: everything this module composes is already
``frontends``/flat-``cli_*``-classified, so this stays a real, direct,
supported CLI-input-translation module (Phase 4's own pattern -- "Move
command input translation into ``frontends/cli/commands``") rather than a
new false-engine-classified wrapper around code the dependency-direction
gate would still flag.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from ...cli_compare_release_helpers import _extract_if_package
from ...cli_compare_release_matrix import _prepare_compare_release_inputs
from ...workflows.extraction import (
    _is_elf_shared_object,
    detect_extractor,
    discover_shared_libraries,
    is_package,
)

# `resolve_release_gate_options`/`GateOptions` are `policy`-classified;
# reached through `workflows.gate`'s own re-export since `frontends` may
# only import `model`/`report`/`workflows` (`check_architecture.py`'s
# dependency-direction rule).
from ...workflows.gate import GateOptions, resolve_release_gate_options

# Module-level (not function-local) so a test can `unittest.mock.patch(
# "abicheck.frontends.cli.release_compare_request.resolve_release_scope_plan",
# ...)` -- the same "unit tests patch the owner module" convention
# `workflows/AGENTS.md` documents for every other module-level import
# binding in this release fan-out (e.g. `cli_compare_release.py`'s own
# `resolve_release_scope_result` binding).
from ...workflows.release_scope import (
    DIRECT_PAIR_KEY,
    release_inventory_evidence,
    resolve_release_scope_plan,
)
from ...workflows.release_stored_inventory import (
    stored_degraded_members,
    stored_side_inventory_complete,
)
from ...workflows.storage import is_project_snapshot_package_dir

if TYPE_CHECKING:
    from ...model.package_inventory import PackageInventory
    from ...model.release_selection import ReleaseSelection
    from ...workflows.release_scope import ReleaseScopePlan
    from ...workflows.release_stored_inventory import StoredDegradedMembers


class _GatePackApplicationLike(Protocol):
    """The structural shape :func:`~abicheck.policy.release_gate_options.
    resolve_release_gate_options` needs from a
    :class:`~abicheck.pack_application.PackApplication` -- a local
    :class:`~typing.Protocol`, not an import of the real class, for the
    identical dependency-direction reason
    ``policy.release_gate_options._GatePackApplication`` (which this
    mirrors) already gives: ``frontends`` may not import the unclassified
    ``pack_application`` module, and a real ``PackApplication`` instance
    satisfies this structurally regardless."""

    @property
    def severity_levels(self) -> Mapping[str, Any]: ...


__all__ = [
    "ReleaseCompareRequest",
    "ReleaseComparePlan",
    "cleanup_release_compare_plan",
    "resolve_release_compare_plan",
]


@dataclass(frozen=True)
class ReleaseCompareRequest:
    """A fully-specified directory/package release-comparison *resolution*
    request -- the release fan-out's counterpart to
    :class:`abicheck.workflows.contracts.CompareRequest`, scoped to the
    pre-execution half (:func:`resolve_release_compare_plan` resolves it;
    it does not itself run any per-library dump/compare).

    Every field here previously reached ``_prepare_compare_release_inputs``/
    ``release_inventory_evidence``/``resolve_release_scope_plan``/
    ``resolve_release_gate_options`` as one of ``compare_release_cmd``'s own
    local variables (parsed straight from Click options); this dataclass is
    the one typed shape both that command and a direct Python caller now
    build.
    """

    old_dir: Path
    new_dir: Path
    debug_info1: Path | None = None
    debug_info2: Path | None = None
    devel_pkg1: Path | None = None
    devel_pkg2: Path | None = None
    include_private_dso: bool = False
    dso_only: bool = False
    headers: tuple[Path, ...] = ()
    old_headers_only: tuple[Path, ...] = ()
    new_headers_only: tuple[Path, ...] = ()
    includes: tuple[Path, ...] = ()
    old_includes_only: tuple[Path, ...] = ()
    new_includes_only: tuple[Path, ...] = ()
    config_includes: tuple[Path, ...] = ()
    old_variant: str | None = None
    new_variant: str | None = None
    #: ADR-065 S1's explicit, identity-keyed selection -- ``None`` (the
    #: default) is D9's narrow current-artifact inference, unchanged.
    release_selection: ReleaseSelection | None = None
    pack_application: _GatePackApplicationLike | None = None
    severity_preset: str | None = None
    severity_abi_breaking: str | None = None
    severity_potential_breaking: str | None = None
    severity_quality_issues: str | None = None
    severity_addition: str | None = None
    on_incomplete_scope: str = "warn"
    fail_on_removed_library: bool = False


@dataclass(frozen=True)
class ReleaseComparePlan:
    """*request*'s resolved pre-execution outcome: everything
    ``compare_release_cmd`` used to compute before its per-library
    comparison loop, as fields rather than locals.

    ``compare_keys`` is ``scope.matched_keys`` with any degraded matched
    member removed -- the exact set the comparison loop below this plan
    actually dumps/compares; a degraded member stays out of it but is still
    named on ``degraded`` (and, downstream, recorded ``failed`` on the
    acquisition record) rather than silently dropped.
    """

    request: ReleaseCompareRequest
    scope: ReleaseScopePlan
    gate: GateOptions
    old_debug_dir: Path | None
    new_debug_dir: Path | None
    old_headers: tuple[Path, ...]
    new_headers: tuple[Path, ...]
    old_includes: tuple[Path, ...]
    new_includes: tuple[Path, ...]
    warnings: tuple[str, ...]
    old_unclassified: dict[str, str] = field(default_factory=dict)
    new_unclassified: dict[str, str] = field(default_factory=dict)
    old_inventory: PackageInventory | None = None
    new_inventory: PackageInventory | None = None
    degraded: StoredDegradedMembers | None = None
    #: Every temporary directory this resolution allocated (package/debug-
    #: package/devel-package extraction, stored-package-variant unpacking),
    #: regardless of whether the caller supplied its own ``make_temp_dir``
    #: or relied on the default one -- see :func:`cleanup_release_compare_plan`.
    temp_dirs: tuple[Path, ...] = ()

    @property
    def compare_keys(self) -> list[str]:
        degraded_matched = self.degraded.matched if self.degraded is not None else {}
        return [k for k in self.scope.matched_keys if k not in degraded_matched]


def cleanup_release_compare_plan(plan: ReleaseComparePlan) -> None:
    """Remove every directory :func:`resolve_release_compare_plan` allocated
    for *plan* (``plan.temp_dirs``) -- the direct-call counterpart of
    ``compare_release_cmd``'s own tracked ``_make_temp_dir``/
    ``_cleanup_temp_dirs`` pair (Codex review, PR #1215: a direct caller that
    lets a package/debug-package/devel-package/stored-package operand use
    the default ``make_temp_dir`` factory would otherwise leak the extracted
    directories for the process's lifetime, since nothing else ever removes
    them). A no-op, per entry, when a directory is already gone."""
    import shutil

    for path in plan.temp_dirs:
        shutil.rmtree(path, ignore_errors=True)


def resolve_release_compare_plan(
    request: ReleaseCompareRequest,
    *,
    make_temp_dir: Callable[[str], Path] | None = None,
) -> ReleaseComparePlan:
    """Resolve *request* into a :class:`ReleaseComparePlan` -- the same
    four-step sequence (``_prepare_compare_release_inputs`` ->
    ``release_inventory_evidence`` -> ``resolve_release_scope_plan`` ->
    ``resolve_release_gate_options``), plus each side's stored-degraded
    markers, that ``compare_release_cmd`` itself now calls this function to
    perform, rather than repeating inline.

    *make_temp_dir* defaults to a plain ``tempfile.mkdtemp``-backed factory
    when omitted; every directory either factory creates -- the caller's own
    or the default -- is recorded on the returned plan's ``temp_dirs``
    (Codex review, PR #1215), so a direct caller can pass the plan to
    :func:`cleanup_release_compare_plan` when done with it.
    ``compare_release_cmd`` passes its own tracked factory and keeps
    removing them in its own ``finally`` block exactly as before this
    function existed; ``plan.temp_dirs`` duplicating that tracking for the
    CLI path is harmless since the CLI never reads it.
    """

    def _do_extract(
        input_path: Path, debug_pkg: Path | None, devel_pkg: Path | None
    ) -> tuple[Path, Path | None, Path | None, Path | None, bool]:
        return _extract_if_package(
            input_path,
            debug_pkg,
            devel_pkg,
            _make_temp_dir,
            is_package,
            detect_extractor,
        )

    def _default_make_temp_dir(prefix: str) -> Path:
        return Path(tempfile.mkdtemp(prefix=prefix))

    _base_make_temp_dir: Callable[[str], Path] = (
        make_temp_dir if make_temp_dir is not None else _default_make_temp_dir
    )
    allocated_temp_dirs: list[Path] = []

    def _make_temp_dir(prefix: str) -> Path:
        path = _base_make_temp_dir(prefix)
        allocated_temp_dirs.append(path)
        return path

    try:
        (
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
        ) = _prepare_compare_release_inputs(
            request.old_dir,
            request.new_dir,
            request.debug_info1,
            request.debug_info2,
            request.devel_pkg1,
            request.devel_pkg2,
            request.include_private_dso,
            request.dso_only,
            request.headers,
            request.old_headers_only,
            request.new_headers_only,
            request.includes,
            request.old_includes_only,
            request.new_includes_only,
            request.config_includes,
            _do_extract,
            discover_shared_libraries,
            is_package,
            _is_elf_shared_object,
            old_variant=request.old_variant,
            new_variant=request.new_variant,
            make_temp_dir=_make_temp_dir,
        )

        old_stored = request.old_dir.is_dir() and is_project_snapshot_package_dir(
            request.old_dir
        )
        new_stored = request.new_dir.is_dir() and is_project_snapshot_package_dir(
            request.new_dir
        )
        old_complete = old_stored and stored_side_inventory_complete(
            request.old_dir, variant_id=request.old_variant
        )
        new_complete = new_stored and stored_side_inventory_complete(
            request.new_dir, variant_id=request.new_variant
        )
        inventory_evidence = release_inventory_evidence(
            old_stored=old_stored,
            new_stored=new_stored,
            old_complete=old_complete,
            new_complete=new_complete,
            direct_pair=list(matched_keys) == [DIRECT_PAIR_KEY],
            new_single_artifact=request.new_dir.is_file()
            and not is_package(request.new_dir),
            old_unclassified=old_unclassified,
            new_unclassified=new_unclassified,
            old_inventory=old_inventory,
            new_inventory=new_inventory,
        )

        plan_matched_keys = matched_keys
        if request.release_selection is not None and list(matched_keys) != [
            DIRECT_PAIR_KEY
        ]:
            plan_matched_keys = [
                k for k in matched_keys if k in request.release_selection
            ]

        scope_plan = resolve_release_scope_plan(
            old_map, new_map, plan_matched_keys, inventory_evidence
        )

        gate = resolve_release_gate_options(
            request.pack_application,
            severity_preset=request.severity_preset,
            severity_abi_breaking=request.severity_abi_breaking,
            severity_potential_breaking=request.severity_potential_breaking,
            severity_quality_issues=request.severity_quality_issues,
            severity_addition=request.severity_addition,
            on_incomplete_scope=request.on_incomplete_scope,
            fail_on_removed_library=request.fail_on_removed_library,
        )

        degraded = stored_degraded_members(
            request.old_dir,
            request.new_dir,
            dict(scope_plan.old_map),
            dict(scope_plan.new_map),
            old_variant=request.old_variant,
            new_variant=request.new_variant,
        )
    except BaseException:
        # Codex review (PR #1215, second finding): a failure partway
        # through resolution -- an old-side archive already extracted
        # before a malformed new-side stored-package variant raises, or a
        # later inventory/degraded-marker read failing -- must not leave
        # whatever this call already allocated behind just because no
        # ReleaseComparePlan was ever returned for a caller to clean up.
        import shutil

        for path in allocated_temp_dirs:
            shutil.rmtree(path, ignore_errors=True)
        raise

    return ReleaseComparePlan(
        request=request,
        scope=scope_plan,
        gate=gate,
        old_debug_dir=old_debug_dir,
        new_debug_dir=new_debug_dir,
        old_headers=tuple(old_h),
        new_headers=tuple(new_h),
        old_includes=tuple(old_inc),
        new_includes=tuple(new_inc),
        warnings=tuple(warning_msgs),
        old_unclassified=dict(old_unclassified),
        new_unclassified=dict(new_unclassified),
        old_inventory=old_inventory,
        new_inventory=new_inventory,
        degraded=degraded,
        temp_dirs=tuple(allocated_temp_dirs),
    )
