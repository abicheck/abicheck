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

"""``abicheck.cli``'s historical import surface, and who owns each name now.

Every name here was once defined in ``abicheck/cli.py``. That module remains
their documented import path -- sibling ``cli_*`` modules and the test suite
both reach for them there -- so ``cli.__getattr__`` resolves each one through
this map at *access* time. A static re-export would re-form the very import
cycle the split removed, since most of these owners import back into ``cli``
for ``main``.

This is data, not logic, and it lives in its own module for one reason: the
root facade has a 150-line budget (ADR-061 Phase 4's acceptance criterion),
and a compatibility table is exactly the kind of thing that should not consume
it. Keeping the table honest is cheap -- ``tests/test_cli_moved_surface.py``
resolves every entry.

Prefer importing from the owner directly in new code; and in tests, patch the
owner, since a ``monkeypatch.setattr`` against a name resolved through here
rebinds nothing the real caller reads.
"""

from __future__ import annotations

import types

MOVED: dict[str, str] = {
    # Snapshot write path (moved earlier, to `cli_buildsource`).
    "_classify_missing_layers": "abicheck.cli_buildsource",
    "_layer_payload_empty": "abicheck.cli_buildsource",
    "_missing_requested_evidence_layers": "abicheck.cli_buildsource",
    "_write_snapshot_output": "abicheck.cli_buildsource",
    # Shared CLI runtime (ADR-061 Phase 4).
    "_AbicheckGroup": "abicheck.frontends.cli.runtime",
    "_EXIT_NOT_COMPARABLE": "abicheck.frontends.cli.runtime",
    "_EXIT_USAGE_ERROR": "abicheck.frontends.cli.runtime",
    "_announce_exit_scheme": "abicheck.frontends.cli.runtime",
    "_collect_metadata": "abicheck.frontends.cli.runtime",
    "_exit_with_severity_or_verdict": "abicheck.frontends.cli.runtime",
    "_finalize_compare_result": "abicheck.frontends.cli.runtime",
    "_load_probe_matrix_changes": "abicheck.frontends.cli.runtime",
    "_log_debug_resolution": "abicheck.frontends.cli.runtime",
    "_log_one_side_debug": "abicheck.frontends.cli.runtime",
    "_render_output": "abicheck.frontends.cli.runtime",
    "_resolve_debug_artifact": "abicheck.frontends.cli.runtime",
    "_safe_write_output": "abicheck.frontends.cli.runtime",
    "_setup_verbosity": "abicheck.frontends.cli.runtime",
    "_stamp_provenance": "abicheck.frontends.cli.runtime",
    "_validate_show_only": "abicheck.frontends.cli.runtime",
    "_warn_all_suppressed": "abicheck.frontends.cli.runtime",
    "_write_or_echo": "abicheck.frontends.cli.runtime",
    # `dump` command input translation.
    "_load_dump_manifest_or_reject": "abicheck.frontends.cli.commands.dump",
    "_resolve_and_check_dump_debug_format": "abicheck.frontends.cli.commands.dump",
    "dump_cmd": "abicheck.frontends.cli.commands.dump",
    # `compare` command input translation.
    "_RELEASE_FORMATS": "abicheck.frontends.cli.commands.compare",
    "_dispatch_release_compare": "abicheck.frontends.cli.commands.compare",
    "_embed_inline_source_side": "abicheck.frontends.cli.commands.compare",
    "_reject_application_operand": "abicheck.frontends.cli.commands.compare",
    "_source_is_pack": "abicheck.frontends.cli.commands.compare",
    "_warn_unused_set_flags": "abicheck.frontends.cli.commands.compare",
    "compare_cmd": "abicheck.frontends.cli.commands.compare",
    # Input resolution & native-dump dispatch (moved earlier, to `cli_resolve`).
    "_apply_native_provenance": "abicheck.cli_resolve",
    "_detect_binary_format": "abicheck.cli_resolve",
    "_dump_native_binary": "abicheck.cli_resolve",
    "_expand_header_inputs": "abicheck.cli_resolve",
    "_is_supported_compare_input": "abicheck.cli_resolve",
    "_looks_like_application": "abicheck.cli_resolve",
    "_maybe_follow_linker_script": "abicheck.cli_resolve",
    "_normalize_binary_input": "abicheck.cli_resolve",
    "_populate_dependency_info": "abicheck.cli_resolve",
    "_resolve_compare_snapshots": "abicheck.cli_resolve",
    "_resolve_input": "abicheck.cli_resolve",
    "_resolve_linker_script": "abicheck.cli_resolve",
    "_sniff_text_format": "abicheck.cli_resolve",
    "classify_compare_operand": "abicheck.cli_resolve",
    # Shared option/parameter helpers (owner: `frontends.cli.options.params`).
    "_load_suppression_and_policy": "abicheck.frontends.cli.options.params",
    # Release fan-out helpers (moved earlier, to `cli_helpers_compare`).
    "_build_match_map": "abicheck.cli_helpers_compare",
    "_canonical_library_key": "abicheck.cli_helpers_compare",
    "_collect_additions": "abicheck.cli_helpers_compare",
    "_collect_force_public_symbols": "abicheck.cli_helpers_compare",
    "_collect_release_inputs": "abicheck.cli_helpers_compare",
    "_merge_gcc_options": "abicheck.cli_helpers_compare",
    "_merge_redundant_changes": "abicheck.cli_helpers_compare",
    "_provenance_timestamp": "abicheck.cli_helpers_compare",
    "_resolve_per_side_options": "abicheck.cli_helpers_compare",
    "_version_sort_key": "abicheck.cli_helpers_compare",
    "_warn_ignored_flags": "abicheck.cli_helpers_compare",
    "_API_BREAK_KINDS": "abicheck.compat.cli",
    "_BINARY_ONLY_KINDS": "abicheck.compat.cli",
    "_NEW_SYMBOL_KINDS": "abicheck.compat.cli",
    "_P2_STUB_FLAGS": "abicheck.compat.cli",
    "_apply_strict": "abicheck.compat.cli",
    "_apply_warn_newsym": "abicheck.compat.cli",
    "_build_internal_suppression": "abicheck.compat.cli",
    "_build_skip_suppression": "abicheck.compat.cli",
    "_build_whitelist_suppression": "abicheck.compat.cli",
    "_classify_compat_error_exit_code": "abicheck.compat.cli",
    "_compat_fail": "abicheck.compat.cli",
    "_detect_compiler_version": "abicheck.compat.cli",
    "_do_echo": "abicheck.compat.cli",
    "_filter_binary_only": "abicheck.compat.cli",
    "_filter_source_only": "abicheck.compat.cli",
    "_limit_affected_changes": "abicheck.compat.cli",
    "_load_descriptor_or_dump": "abicheck.compat.cli",
    "_load_skip_headers": "abicheck.compat.cli",
    "_merge_suppression": "abicheck.compat.cli",
    "_resolve_headers_from_list": "abicheck.compat.cli",
    "_safe_path": "abicheck.compat.cli",
    "_setup_logging": "abicheck.compat.cli",
    "_warn_stub_flags": "abicheck.compat.cli",
    "_write_affected_list": "abicheck.compat.cli",
}


class _FacadeModule(types.ModuleType):
    """A module that refuses to have a moved name *set* on it.

    ``abicheck.cli`` resolves every name in :data:`MOVED` lazily through a
    module-level ``__getattr__``, which only fires while the name is absent
    from that module's globals -- so anything that assigns one permanently
    shadows the lazy lookup with a frozen reference. A ``monkeypatch.setattr``
    against the facade is enough: it records the lazily-resolved original and
    re-assigns it on undo. Every later caller then reads that stale original,
    and every later test that patches the *true* owner is silently ignored.
    Not hypothetical -- it landed as an order-dependent CI failure two test
    files away from the one that caused it. Raising turns the whole class of
    leak into an error at the point of the mistake, naming the owner to patch.
    """

    def __setattr__(self, name: str, value: object) -> None:
        """Reject a moved name; anything else assigns as on a plain module."""
        owner = MOVED.get(name)
        if owner is not None:
            raise AttributeError(
                f"{self.__name__}.{name} is a compatibility alias resolved "
                f"lazily from {owner!r}; setting it here would shadow that "
                f"lookup for the rest of the process. "
                f"Patch {owner}.{name} instead."
            )
        super().__setattr__(name, value)


def install_facade_guard(module: types.ModuleType) -> None:
    """Make ``module`` reject assignment of any name :data:`MOVED` owns."""
    module.__class__ = _FacadeModule
