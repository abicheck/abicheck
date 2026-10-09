# SPDX-License-Identifier: Apache-2.0
"""Which abicheck code shapes a stored clang header-AST cache entry.

The header-AST cache (``dumper_ast_config._cache_key``) stores an external
tool's output, so its key is the tool's *input* -- the headers, the toolchain,
and the exact aggregate header and command line abicheck generates (folded in
as an invocation digest, so a change to how abicheck builds either is a key
change by construction). For castxml that is the whole story: its XML is
stored byte for byte.

A clang entry is not raw output. Between clang exiting and the entry being
written, abicheck code decides what is stored: it compacts the document, selects
one document out of a DPC++ multi-pass stream, retries with failing headers
removed (changing which declarations the entry holds), and writes the
parse-exclusions sidecar the entry is read back with. Those modules are named
here and their source is folded into every clang key
(:func:`clang_ast_output_fingerprint`), so editing any of them is a miss --
without folding the whole package, which would make the cache miss on every
unrelated change.

``tests/test_header_ast_cache_key_inputs.py`` keeps this list honest: it traces
a real clang header dump and fails if any abicheck module runs between clang's
exit and the cache write without being listed here or in
:data:`NON_SHAPING_MODULES`.
"""

from __future__ import annotations

from ..storage.code_identity import abicheck_modules_fingerprint

__all__ = [
    "CLANG_AST_OUTPUT_MODULES",
    "NON_SHAPING_MODULES",
    "clang_ast_output_fingerprint",
]

#: Modules whose code determines the bytes of a stored clang entry.
CLANG_AST_OUTPUT_MODULES: tuple[str, ...] = (
    # Decides what is published: compacted single document vs. the selected
    # DPC++ document, and whether a result is written at all.
    "abicheck.dumper_clang_errors",
    # The compact ASCII copy that is the stored entry on the plain path.
    "abicheck.storage.json_compact",
    # DPC++: picks the stored document out of the multi-pass stream.
    "abicheck.sycl_context",
    # DPC++: plans the device/host job replay whose host pass emits the
    # stored document.
    "abicheck.buildsource.dpcpp_jobs",
    # Retries with failing headers removed; the stored entry then covers
    # fewer headers than were requested.
    "abicheck.extract.headers.clang.error_header_retry",
    # The sidecar stored beside the entry, recording what was excluded.
    "abicheck.storage.ast_parse_exclusions",
)

#: Modules that run on that path without shaping what is stored, each with
#: why. A module the trace finds that is in neither tuple fails the test.
NON_SHAPING_MODULES: dict[str, str] = {
    "abicheck.deadline": "budget checks only",
    "abicheck.storage.header_ast_cache": "coordination and memo bookkeeping",
    "abicheck.dumper_clang_streaming": "prunes the in-memory tree after the entry is written",
    "abicheck.extract.env_flags": "reads the prune switch, which acts on the in-memory tree only",
    "abicheck.extract.headers.clang.locations": "materializes locations after the cache write",
    "abicheck.storage.acyclic_json": "pauses the garbage collector",
    "abicheck.storage.ast_size_observer": "size telemetry",
    "abicheck.workflows.memory_trace": "memory telemetry, called through the size observer's hook when installed",
    "abicheck.storage.cache_integrity": "digests the written entry for read-time verification",
    "abicheck.storage.derived_ast": "offers the on-disk document to a derived consumer",
    # The failing-header retry calls back into the run to rebuild clang's
    # input for the reduced header set. That input comes from the same
    # builders the key's invocation digest calls, so it is keyed already.
    "abicheck.extract.headers.clang.backend": "rebuilds the aggregate for a retry (`clang_aggregate_text`, keyed)",
    "abicheck.dumper_ast_config": "rebuilds the command line for a retry (keyed builder)",
    "abicheck.dumper_clang": "command-line helpers of that keyed builder",
    "abicheck._compiler_options": "command-line helpers of that keyed builder",
    "abicheck.header_utils": "command-line helpers of that keyed builder",
}


def clang_ast_output_fingerprint() -> str:
    """Fingerprint of :data:`CLANG_AST_OUTPUT_MODULES`' source (memoized)."""
    return abicheck_modules_fingerprint(CLANG_AST_OUTPUT_MODULES)
