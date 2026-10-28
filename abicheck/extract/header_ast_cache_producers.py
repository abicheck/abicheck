# SPDX-License-Identifier: Apache-2.0
"""Which abicheck code shapes a stored header-AST cache entry.

The header-AST cache (``extract.headers.ast_config._cache_key``) stores an external
tool's output, so its key is the tool's *input* -- the headers, the toolchain,
and the exact aggregate header and command line abicheck generates (folded in
as an invocation digest, so a change to how abicheck builds either is a key
change by construction).

Neither entry is raw tool output. Between the tool exiting and the entry being
written, abicheck code decides what is stored:

* clang: compacts the document, selects one document out of a DPC++
  multi-pass stream, retries with failing headers removed (changing which
  declarations the entry holds), and writes the parse-exclusions sidecar the
  entry is read back with;
* castxml: splices into the XML document the preprocessed text of the
  calling-convention declarations and the preprocessor-excluded declaration
  scan (``extract.headers.castxml.preprocessed_text`` and
  ``extract.headers.preprocessor_branches``), so a cached document carries
  abicheck-computed evidence, not only castxml's output.

Those modules are named here per backend, and each backend's source is folded
into its own keys (:func:`clang_ast_output_fingerprint`,
:func:`castxml_ast_output_fingerprint`), so editing any of them is a miss --
without folding the whole package, which would make the cache miss on every
unrelated change.

``tests/test_header_ast_cache_key_inputs.py`` keeps these lists honest: it traces
a real clang header dump and a real castxml dump, and fails if any abicheck
module runs between the tool's exit and the cache write without being listed
here or in :data:`NON_SHAPING_MODULES`.
"""

from __future__ import annotations

from ..storage.code_identity import abicheck_modules_fingerprint

__all__ = [
    "CASTXML_AST_OUTPUT_MODULES",
    "CLANG_AST_OUTPUT_MODULES",
    "NON_SHAPING_MODULES",
    "castxml_ast_output_fingerprint",
    "clang_ast_output_fingerprint",
]

#: Modules whose code determines the bytes of a stored castxml entry: the
#: document that is published is castxml's XML plus what abicheck splices into
#: it, and whether a result is published at all.
CASTXML_AST_OUTPUT_MODULES: tuple[str, ...] = (
    # Runs the castxml step, splices the calling-convention and hidden-scan
    # payloads into the document, validates the output and publishes it.
    "abicheck.extract.headers.castxml.backend",
    # The preprocessed-text table the splice carries; decides which
    # declarations are kept.
    "abicheck.extract.headers.castxml.preprocessed_text",
    # Decides which declarations spell a calling convention (the splice's
    # trigger for the preprocessed text).
    "abicheck.extract.headers.castxml.calling_convention",
    # The attribute vocabulary that module matches against (a constant read,
    # which a call trace cannot observe).
    "abicheck.model.cc_attributes",
    # The toolchain-metadata key probe.py writes the excluded-header list under.
    "abicheck.model.header_exclusion_record",
    # The serialized preprocessor-excluded scan the document carries.
    "abicheck.extract.headers.castxml.hidden_declarations",
    # Computes that scan from the preprocessed run.
    "abicheck.extract.headers.preprocessor_branches",
    # Runs castxml and its preprocess sibling, and parses their output for
    # validation, which decides whether a result is written.
    "abicheck.extract.headers.castxml.probe",
    "abicheck.storage.castxml_xml",
    # The stored entry's envelope (dependency digests, the payload written).
    "abicheck.storage.tu_parse_store",
    "abicheck.storage.guards",
)

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
    "abicheck.extract.dpcpp_jobs",
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
    "abicheck.extract.headers.clang.streaming": "prunes the in-memory tree after the entry is written",
    "abicheck.extract.env_flags": "reads the prune switch, which acts on the in-memory tree only",
    "abicheck.extract.headers.clang.locations": "materializes locations after the cache write",
    "abicheck.storage.acyclic_json": "pauses the garbage collector",
    "abicheck.storage.ast_size_observer": "size telemetry",
    "abicheck.workflows.memory_trace": "memory telemetry, called through the size observer's hook when installed",
    "abicheck.storage.cache_integrity": "digests the written entry for read-time verification",
    "abicheck.storage.ast_cache_location": "names where the cache lives, not what an entry holds",
    "abicheck.storage.derived_ast": "offers the on-disk document to a derived consumer",
    # Resolves the clang binary and SYCL mode before clang runs; the failing-header
    # retry re-resolves the same inputs. The resolved binary is already keyed by
    # the cache's tool identity, so this decision does not shape the stored entry.
    "abicheck.extract.compiler_driver": "pure driver and SYCL-mode decision over inputs already in the cache key",
    # The failing-header retry calls back into the run to rebuild clang's
    # input for the reduced header set. That input comes from the same
    # builders the key's invocation digest calls, so it is keyed already.
    "abicheck.extract.headers.clang.backend": "rebuilds the aggregate for a retry (`clang_aggregate_text`, keyed)",
    "abicheck.extract.headers.ast_config": "rebuilds the command line for a retry (keyed builder)",
    "abicheck.extract.headers.clang_header_argv": "the body of that keyed builder, rendering the same argv",
    "abicheck.dumper_clang": "command-line helpers of that keyed builder",
    "abicheck._compiler_options": "command-line helpers of that keyed builder",
    "abicheck.header_utils": "command-line helpers of that keyed builder",
    # castxml path (CASTXML_AST_OUTPUT_MODULES' trace).
    "abicheck.model.execution_cache": "memo bookkeeping around the parsed tree",
    "abicheck.extract.headers.toolchain": "reads the tool identities the key already folds, to detect a toolchain change mid-run",
    "abicheck.storage.atomic_file": "byte-faithful atomic publish of the text the backend already shaped",
}


def clang_ast_output_fingerprint() -> str:
    """Fingerprint of :data:`CLANG_AST_OUTPUT_MODULES`' source (memoized)."""
    return abicheck_modules_fingerprint(CLANG_AST_OUTPUT_MODULES)


def castxml_ast_output_fingerprint() -> str:
    """Fingerprint of :data:`CASTXML_AST_OUTPUT_MODULES`' source (memoized)."""
    return abicheck_modules_fingerprint(CASTXML_AST_OUTPUT_MODULES)
