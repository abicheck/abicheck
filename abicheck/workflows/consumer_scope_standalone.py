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

"""Standalone consumer checks that run the library comparison themselves.

:func:`check_appcompat` and :func:`check_plugin_host_contract` dump/compare
the old and new library, then delegate the scoping to
``workflows.consumer_scope``. Each is the orchestrator for its single
consumer, so each makes the one ``close_consumer_scope`` call (ADR-067).
When a diff already exists, call ``scope_diff_to_app``/
``scope_diff_to_required_symbols`` directly instead.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from ..model import AbiSnapshot
from ..model.consumer_spec import ConsumerAppInput
from ..policy.disposition_close import close_consumer_scope, ledger_for
from .consumer_scope import (
    AppCompatResult,
    PluginHostContractResult,
    scope_diff_to_app,
    scope_diff_to_required_symbols,
)

if TYPE_CHECKING:
    from ..policy_file import PolicyFile
    from ..suppression import SuppressionList


def check_appcompat(
    app_path: ConsumerAppInput,
    old_lib_path: Path,
    new_lib_path: Path,
    *,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    old_headers: list[Path] | None = None,
    new_headers: list[Path] | None = None,
    old_includes: list[Path] | None = None,
    new_includes: list[Path] | None = None,
    old_version: str = "old",
    new_version: str = "new",
    lang: str = "c++",
    suppression: SuppressionList | None = None,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    scope_to_public_surface: bool = True,
) -> AppCompatResult:
    """Check application compatibility with a library update (standalone).

    Dumps and compares the two libraries itself, then delegates the app-usage
    scoping to :func:`scope_diff_to_app`. When a diff already exists (e.g.
    inside ``compare``'s own pipeline), call :func:`scope_diff_to_app` directly
    instead of re-dumping/re-comparing through this wrapper.
    """
    # Run standard library comparison, routed through the real workflows-
    # package owners (ADR-037 D1/D10.1's original T5 direct-bypass migration
    # target) rather than `dumper.dump()`/`checker.compare()` directly, and
    # rather than the flat `abicheck.service` facade: `service.py` also
    # re-exports frontends-classified `service_render.render_output`, and
    # this module is workflows-classified, so importing `service.py` itself
    # here would widen that workflows -> frontends edge instead of letting
    # it close (ADR-061 gap A). Lazy import avoids a
    # workflows.input_resolution→cli→appcompat import cycle.
    from ..errors import ValidationError
    from .compare_policy import compare_snapshots
    from .dump.native import run_dump
    from .input_resolution import detect_binary_format

    # Resolve per-side headers: old_headers/new_headers override shared headers
    _old_h = old_headers if old_headers is not None else (headers or [])
    _new_h = new_headers if new_headers is not None else (headers or [])
    _old_inc = old_includes if old_includes is not None else (includes or [])
    _new_inc = new_includes if new_includes is not None else (includes or [])

    old_fmt = detect_binary_format(old_lib_path)
    new_fmt = detect_binary_format(new_lib_path)
    if old_fmt is None or new_fmt is None:
        bad = old_lib_path if old_fmt is None else new_lib_path
        raise ValidationError(f"Unrecognised binary format for {bad}")

    # `run_dump`'s dependency-scope wrapper defaults `include_dependencies`
    # to True: suppresses the streaming pruner (this call's own pre-migration
    # `suppress_streaming_prune()`) and tags `dependency_scope`, unlike a
    # direct `dumper.dump()` call. `public_include_search_dirs` is this
    # caller's own genuine, explicit -I list (never auto-derived), same as
    # `_dump_elf`'s own wiring, so an explicit include root promotes its
    # declarations to PUBLIC_HEADER here too.
    old_snap = run_dump(
        old_lib_path,
        old_fmt,
        _old_h,
        _old_inc,
        old_version,
        lang,
        public_headers=list(_old_h),
        public_include_search_dirs=list(_old_inc),
    )
    new_snap = run_dump(
        new_lib_path,
        new_fmt,
        _new_h,
        _new_inc,
        new_version,
        lang,
        public_headers=list(_new_h),
        public_include_search_dirs=list(_new_inc),
    )
    # ADR-075 D1/D2: record ownership for what this run extracted.
    from .snapshot_factory import finish_ownership

    old_snap = finish_ownership(old_snap, None, list(_old_h), list(_old_inc))
    new_snap = finish_ownership(new_snap, None, list(_new_h), list(_new_inc))

    # Route through the real workflows owner; ADR-037 D1.
    diff = compare_snapshots(
        old_snap,
        new_snap,
        suppression=suppression,
        policy=policy,
        policy_file=policy_file,
        scope_to_public_surface=scope_to_public_surface,
    )

    scoped = scope_diff_to_app(
        diff,
        app_path,
        old_lib_path,
        new_lib_path,
        policy=policy,
        policy_file=policy_file,
        suppression=suppression,
        # ADR-057: old_lib_path is a real binary, so the graph the dump above
        # already attached is only reachable through the snapshot itself.
        old_snapshot=old_snap,
    )
    # ADR-067: `scope_diff_to_app` leaves the ledger open (the `--used-by`
    # path calls it once per consumer and only the orchestrator knows the
    # union); this entry point *is* that orchestrator for its single consumer,
    # so the one closing call is here. `also_detected` is the whole relevant
    # set rather than a finding-id-filtered one: it holds the very
    # `diff.changes` objects, so `record`'s identity keying no-ops on those,
    # newly recording only the scoped-only findings.
    relevant = scoped.breaking_for_app
    close_consumer_scope(
        ledger_for(diff), diff, gating=relevant, also_detected=relevant
    )
    return scoped


# ---------------------------------------------------------------------------
# Weak mode: check-against (no old library needed)
# ---------------------------------------------------------------------------


def check_plugin_host_contract(
    old_plugin: AbiSnapshot,
    new_plugin: AbiSnapshot,
    required_entrypoints: Iterable[str],
    *,
    suppression: SuppressionList | None = None,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
) -> PluginHostContractResult:
    """Check whether a plugin upgrade still satisfies a host's load contract.

    Given two snapshots of a plugin (old/new) and the set of entry-point
    symbols a *host* resolves from it (a manifest, or symbols a host binary
    exports back to the plugin), report whether the new plugin still satisfies
    the host — the plugin-load mirror of :func:`check_appcompat`. Runs the
    comparison itself; when a diff already exists, call
    :func:`scope_diff_to_required_symbols` directly instead.
    """
    # Route through the real workflows owner, not the flat `abicheck.service`
    # facade (ADR-061 gap A; see `check_appcompat`'s own comment above for
    # why). Lazy import avoids an import cycle.
    from .compare_policy import compare_snapshots

    diff = compare_snapshots(
        old_plugin,
        new_plugin,
        suppression=suppression,
        policy=policy,
        policy_file=policy_file,
    )

    scoped = scope_diff_to_required_symbols(
        diff,
        old_plugin,
        new_plugin,
        required_entrypoints,
        policy=policy,
        policy_file=policy_file,
        suppression=suppression,
    )
    # ADR-067: the standalone plugin-host entry point is the orchestrator for
    # its own single host contract, exactly as `check_appcompat` is for its
    # consumer -- so it makes the one closing call too. Without it a plugin
    # that drops an unrelated export while keeping every required entrypoint
    # correctly returns COMPATIBLE while the audit still calls that removal
    # `gating` (Codex review; the repo-wide sweep for `close_consumer_scope`
    # call sites is what this closes).
    relevant = scoped.breaking_for_host
    close_consumer_scope(
        ledger_for(diff), diff, gating=relevant, also_detected=relevant
    )
    return scoped
