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

"""Bug classes about *which evidence* a decision was read from.

A sibling of `manifest_guards.py`/`manifest_report.py`/
`manifest_tool_surface.py` (see `manifest.py` for what this registry is and
is not). The classes here are not about a wrong computation over the right
input: they are about a detector being handed, or reaching for, an
observation that does not answer the question it is asking.

`evidence.stored_snapshot_rederivation` and
`coverage.discovery_derived_completeness` share a root cause worth naming
once: both are a check answering a question its *evidence* does not support --
one by reading today's filesystem for a historical fact, the other by treating
whatever a discovery pass happened to find as the full set it was supposed to
look at.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["EVIDENCE_BUG_CLASSES"]


EVIDENCE_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="evidence.export_presence_as_declaration_presence",
        invariant=(
            "Three independent observations about a declaration -- (a) a "
            "header this run parsed declares it, (b) it belongs to the "
            "promised public contract for this run's scope/contract "
            "selection, (c) the artifact's export table carries a symbol "
            "for it -- are answered separately, from the evidence that "
            'actually bears on each, and each keeps a real "unknown". A '
            "detector filters on the one it needs: a source-declaration "
            "population is built from (a)/(b) and never from (c), so a "
            "version-script or -fvisibility change that stops exporting a "
            "byte-identical declaration is reported as the binary-axis "
            "change it is and never as a removed source API; a promised, "
            "declared, unexported inline function is in the public surface "
            "rather than misfiled out of it. Weaker evidence narrows the "
            "conclusion: a snapshot with no headers answers (a) unknown, "
            "never False, and discarding header evidence (an evidence-depth "
            "projection) returns it to unknown rather than asserting its "
            "negation. The axis is symmetric and complete in both "
            "directions: an export that *appears* on an existing "
            "declaration is recorded as a compatible addition, never "
            "dropped because the matched pair reaches no added-symbol "
            "path, and a detector whose subject is a binary symbol filters "
            "on the *intersection* of (b) and (c) rather than their union, "
            "so a promised-but-unexported declaration never stands in for "
            "an export the artifact does not have."
        ),
        # Reported against a real comparison alongside the three
        # name-shape defects PR #1231 fixed; recorded as its own gap at the
        # time (it needed a model/schema change, not a call-site patch) and
        # closed by the split this entry registers.
        fixed_by=(1231,),
        seed_tests=("tests/test_surface_fact_split.py",),
        axes={
            "fact": ("declared_in_headers", "in_public_contract", "binary_exported"),
            "state": ("true", "false", "unknown"),
            "direction": ("lost", "gained", "unchanged"),
            "combination": ("union", "intersection", "single-fact"),
            "producer": ("header-ast", "debug-info", "export-table", "projection"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The seed test enumerates the whole 3x7 fact domain and "
                    "both real detector outcomes through `checker.compare`, "
                    "but every snapshot it builds is constructed in-process. "
                    "The producers are covered at their own boundary "
                    "(`export_symbol_identity`, "
                    "`extract.surface_fact_producers`, the provenance pass) "
                    "rather than by a real castxml/clang dump of a library "
                    "whose two builds differ only in a version script -- the "
                    "exact shape the original report came from -- so a "
                    "regression in how a *live* header-AST dump populates "
                    "the three facts would be caught by the integration "
                    "lanes, not by this class's own generalized test. "
                    "Adding that case needs a fixture pair with a version "
                    "script, which the marker lanes (`integration`) own."
                ),
                reference=(
                    "docs/contribute/known-gaps.md, the `Visibility.PUBLIC` entry"
                ),
            ),
        ),
    ),
    BugClass(
        id="evidence.stored_snapshot_rederivation",
        invariant=(
            "A path recorded in a snapshot is provenance, not a licence to "
            "re-read the current filesystem for a historical fact. Any "
            "analysis of a stored snapshot must answer from what that "
            "snapshot carries, or from an explicitly supplied, "
            "provenance-verified source context for that side -- never from "
            "whatever now lives at a same-looking path on the current "
            "runner. So a stored snapshot's derived facts are invariant "
            "under every mutation of the source checkout it names (edited, "
            "truncated, substituted, deleted, relocated, made unreadable, "
            "replaced by a directory), and where the confirmed historical "
            "facts are absent the result states that the historical "
            "evaluation was not possible rather than substituting today's. "
            "The licence is resolved **per evidence source**, not per side: "
            "one source's live provenance never licenses reading another's "
            "recorded paths, and a partially-licensed side reports the half "
            "it read while establishing no absence over the half it did not."
        ),
        fixed_by=(1141, 1236),
        seed_tests=("tests/test_stored_snapshot_source_licence.py",),
        axes={
            "side_provenance": (
                "live_extraction",
                "stored_snapshot",
                "verified_context",
            ),
            "front_end": ("cli", "typed_python_api", "abicc_compat"),
            # A side holds up to two independently-provenanced source-evidence
            # sources at once, and each is licensed on its own: a live header
            # dump merged with a pre-captured --build-info pack is live for its
            # declared headers and historical for the pack's compile units.
            "evidence_source": ("declared_headers", "embedded_build_pack"),
            "source_mutation": (
                "unchanged",
                "delete_file",
                "edit_file",
                "truncate_file",
                "substitute_unrelated_file",
                "relocate_tree",
                "replace_file_with_directory",
                "make_unreadable",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Only the pattern/preprocessor pre-scan reads recorded "
                    "source paths today, so the licence is enforced at that "
                    "one workflow plus the two primitives under it. Nothing "
                    "mechanically forbids a *future* consumer from reading "
                    "AbiSnapshot.source_header (or a compile unit's source) "
                    "without resolving a licence first -- that would need an "
                    "AI-readiness-style AST gate of its own, the way "
                    "fact-field-readers guards Fact[T] reads."
                ),
                reference="docs/contribute/known-gaps.md",
                canary_test=None,
            ),
            KnownGap(
                description=(
                    "The licence is object-level, not content-verified: a "
                    "live extraction grants it for the whole run rather than "
                    "digesting each source file and re-checking it at read "
                    "time. Persisting per-input digests (a snapshot schema "
                    "bump) would let a stored snapshot re-derive safely when "
                    "the tree genuinely matches, instead of declining."
                ),
                reference="docs/contribute/known-gaps.md",
                canary_test=None,
            ),
        ),
    ),
    BugClass(
        id="coverage.discovery_derived_completeness",
        invariant=(
            "Sufficiency for an *absence* claim is computed from the "
            "**expected** input set, never from what a discovery pass "
            "happened to find. Every declared input is accounted for by "
            "exactly one disposition -- scanned, missing, unreadable, "
            "unsupported, deliberately excluded, or not licensed -- and any "
            "of those gap states keeps the side insufficient, so a root that "
            "did not exist can never yield 'fully covered'. Sufficiency is "
            "answered per check (each check answering to its own evidence), "
            "not by one global tally. Establishment is then per finding: "
            "presence is established by observation, absence only by "
            "sufficiency, so a fold may claim 'introduced'/'resolved' only "
            "when the side whose *absence* it asserts is established, and an "
            "undecidable identity is reported as not-evaluated rather than "
            "dropped."
        ),
        fixed_by=(1141,),
        seed_tests=(
            "tests/test_source_input_completeness.py",
            "tests/test_pattern_preprocessor_scan_coverage.py",
            "tests/test_preprocessor_probe_families.py",
        ),
        axes={
            "disposition": (
                "selected",
                "scanned",
                "missing",
                "unreadable",
                "unsupported",
                "excluded",
                "not_licensed",
            ),
            "check": ("pattern_escalation", "macro_divergence", "header_leak"),
            "probe_family": ("macro", "header"),
            "input_failure_mode": (
                "missing_root",
                "unreadable_file",
                "unenumerable_directory",
                "unexaminable_candidate",
                "unstattable_candidate",
                "unsupported_node",
            ),
            "fold_state": (
                "persistent",
                "introduced",
                "resolved",
                "not_evaluated",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "A file the classifier cannot *examine* must be told apart "
                    "from a file that was never evidence: a two-state "
                    "scannable/not-scannable predicate collapses them, and the "
                    "unreadable one then vanishes from the account instead of "
                    "registering as a gap. The lexical pattern scan's sufficiency is "
                    "per *check*, "
                    "but its one check shares a single expected-input set: a "
                    "gap in any declared root makes every PatternKind's "
                    "absence unestablished, since a lexical scan cannot say "
                    "which kind an unread file would have contained. A finer "
                    "per-kind answer would need per-kind input attribution "
                    "the scanner does not have."
                ),
                reference="abicheck/workflows/pattern_preprocessor_scan.py",
                canary_test=None,
            ),
        ),
    ),
    BugClass(
        id="evidence.container_presence_read_as_evidence_content",
        invariant=(
            "Whether evidence was collected is answered from the evidence's "
            "own content flag, never from whether a container object was "
            "attached to hold it. An extractor that ran and found nothing, "
            "one that was never asked, and one that raised all attach the "
            "same empty, truthy metadata object, so object presence is a "
            "fact about the pipeline's shape and carries no information "
            "about the artifact; reading it as evidence reports a stripped "
            "binary as fully debug-covered and makes the lower evidence tier "
            "unreachable on the normal path. The answer is consistent across "
            "every consumer of the same fact -- coverage row, evidence tier, "
            "key-shape trust, and detector support gate -- since a detector "
            "declared supported over empty input reports 'ran, found "
            "nothing' where the truth is 'never evaluated'. Status, "
            "confidence and human-readable detail agree with one another, so "
            "no single corrected field leaves a reader equally misled, and "
            "no-evidence-in-hand is reported as such rather than as a claim "
            "about what the artifact contains."
        ),
        fixed_by=(1268,),
        seed_tests=("tests/test_debug_evidence_presence.py",),
        public_surfaces=("compare --format json: layer_coverage[].status",),
        axes={
            "slot": ("dwarf", "dwarf_advanced"),
            "state": ("absent", "empty", "empty_with_payload_keys", "collected"),
            "consumer": (
                "intrinsic_coverage",
                "surface_graph evidence tier",
                "typedef key-shape trust",
                "detector support gate",
            ),
            "producer": ("elf-dump", "symbols-only fallback", "btf/ctf", "g++ -g/-g0"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The sweep is exhaustive over the two debug slots, which "
                    "is where the reported defect lived, but the underlying "
                    "shape -- a dataclass with no __bool__ standing in for "
                    "its own content flag -- is not confined to them. There "
                    "is no repo-wide gate (an AST check in the shape of "
                    "`fact-detector-misuse`) that would fail a newly "
                    "introduced `if snap.<metadata>` presence test on some "
                    "other extractor's slot, so a sibling instance elsewhere "
                    "is caught by review rather than mechanically."
                ),
                reference="abicheck/model/dwarf_facts.py, debug_info_present",
                canary_test=None,
            ),
        ),
    ),
    BugClass(
        id="extraction.implicit_language_rule_read_off_one_explicit_key",
        invariant=(
            "A language-level property is extracted from the language's own "
            "rule, not from the one syntactic key a particular frontend "
            "happens to emit for the explicit spelling of it. Where a rule "
            "has an explicit form and implicit forms (inline: the keyword, "
            "but also constexpr/consteval, a member defined in its class "
            "body, and an in-class `= default`), every form yields the same "
            "answer, and a backend whose serialization omits the implicit "
            "ones reconstructs them from what it does carry rather than "
            "reporting the negation. The property therefore agrees across "
            "backends for the same declaration, which is what keeps a "
            "baseline captured with one frontend comparable against a "
            "candidate captured with another, and what makes a declared "
            "parity claim true. The reconstruction is bounded by the same "
            "rule in both directions: a member merely declared in its class "
            "and an out-of-line definition at namespace scope keep the "
            "negative answer, so removing a false positive never introduces a "
            "false negative in the consumer -- here an export obligation, "
            "whose whole purpose is to flag exactly those. A `= delete`d "
            "function is on the *positive* side, at any scope "
            "([dcl.fct.def.delete]/4); it was briefly treated as negative, "
            "which made an inline function becoming `= delete` emit a "
            "spurious FUNC_LOST_INLINE beside the true FUNC_DELETED. It keeps "
            "no export obligation for an unrelated reason -- the consumer "
            "filters `is_deleted` on its own axis -- which is precisely why "
            "the two must not be conflated."
        ),
        fixed_by=(1268,),
        seed_tests=("tests/test_clang_inline_semantics.py",),
        public_surfaces=(
            "compare --format json: changes[].kind == public_not_exported",
        ),
        axes={
            "form": (
                "explicit inline",
                "constexpr",
                "consteval",
                "in-class definition",
                "in-class = default",
                "in-class hidden friend",
                "unnamed-record member",
                "function-try-block body",
                "coroutine body",
                "= delete (any scope)",
            ),
            "negative_form": (
                "declared-only member",
                "out-of-line definition",
                "out-of-line = default",
                "free function with body",
                "anonymous namespace",
            ),
            "decl_kind": (
                "FunctionDecl",
                "CXXMethodDecl",
                "CXXConstructorDecl",
                "CXXDestructorDecl",
                "CXXConversionDecl",
            ),
            "scope": ("translation unit", "namespace", "record", "nested record"),
            "backend": ("clang JSON AST", "castxml"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "No reliability flag guards `Function.is_inline` across "
                    "evidence generations, so a baseline dumped with the "
                    "clang backend *before* this fix -- which recorded the "
                    "wrong value -- compares against a post-fix candidate as "
                    "a wave of spurious FUNC_BECAME_INLINE. Bounded (castxml "
                    "is the default backend and always recorded these as "
                    "inline; the findings are RISK-class) and cleared by "
                    "re-dumping the baseline. The complete fix is a "
                    "`clang_inline_facts_reliable` flag on the established "
                    "`*_facts_reliable` pattern, which needs a SCHEMA_VERSION "
                    "bump to be able to tell the two generations apart at "
                    "all, hence its own ADR and migration."
                ),
                reference=(
                    "docs/contribute/known-gaps.md, the "
                    "clang_inline_facts_reliable entry"
                ),
                canary_test=None,
            ),
            KnownGap(
                description=(
                    "Templates are still excluded from the export obligation "
                    "by `_looks_templated`, a spelling heuristic over the "
                    "declaration's name rather than a template-ness fact, so "
                    "an uninstantiated template whose name carries no angle "
                    "brackets rests on a different, weaker mechanism than "
                    "the one this class fixed. The cross-backend "
                    "differential also compares only declarations both "
                    "frontends key identically: castxml spells ctors/dtors "
                    "with synthetic keys, so those are excluded from the "
                    "comparison rather than verified across backends. That "
                    "has one concrete consequence worth naming: of the four "
                    "rules the predicate implements, three are caught by the "
                    "cross-backend oracle on revert, but the in-class "
                    "`= default` branch is caught only by a hand-written "
                    "case. Clang synthesizes a real body for a defaulted "
                    "member it odr-uses, so `_has_body` already covers those; "
                    "the branch only decides a defaulted member clang leaves "
                    "bodiless, which is precisely the ctor/dtor family the "
                    "differential cannot key. A shape was not contrived to "
                    "close this -- it is recorded instead. Separately, "
                    "C++20 defaulted comparisons have no cross-backend oracle "
                    "at all: castxml 0.7.0 cannot parse `operator<=>`, so "
                    "that shape is asserted against clang alone."
                ),
                reference=(
                    "abicheck/buildsource/cross_source_checks.py, "
                    "_has_export_obligation"
                ),
                canary_test=None,
            ),
        ),
    ),
    BugClass(
        id="evidence.operand_shape_silently_unresolved",
        invariant=(
            "A member the caller explicitly named resolves to the same "
            "evidence regardless of the *spelling* of the operand carrying "
            "it -- a directory-backed ProjectSnapshot package and a loose "
            "snapshot file (plain, gzip or zstd; flat or sectioned) are two "
            "spellings of one stored artifact, not two evidence levels. A "
            "resolver that recognises only some spellings must never reduce "
            "the rest to 'absent': an operand shape silently dropped for "
            "being unrecognised is indistinguishable, downstream, from a "
            "library that genuinely is not there -- and an emptied OLD-side "
            "graph then scores as a clean pass with every real cross-DSO "
            "break unreported, which is AGENTS.md's 'absent is not removed' "
            "read at the operand layer rather than the inventory layer."
        ),
        fixed_by=(1269,),
        seed_tests=("tests/test_bundle_stored_file_members.py",),
        public_surfaces=(),
        known_gaps=(
            KnownGap(
                description=(
                    "Resolution is fixed, but the *disposition* is not: an "
                    "unresolvable stored member is still skipped with a log "
                    "line rather than recorded on the comparison scope, so a "
                    "release whose members all fail to resolve can still "
                    "report a compatible bundle verdict. Closing that is an "
                    "ADR-065 scope-record decision (the `unsupported`/"
                    "`failed` member states), not a resolver change, and the "
                    "skip-not-raise contract is separately pinned by "
                    "tests/test_cli_compare_release_evidence_preservation.py."
                ),
                reference="abicheck/bundle.py::build_bundle_snapshot_mixed",
                canary_test=None,
            ),
            KnownGap(
                description=(
                    "The bundle sniff is not the first gate: "
                    "`workflows.release_inputs.collect_release_inputs` "
                    "filters a release directory through "
                    "`classify.AbiJsonClassifier`, whose own 4096-byte probe "
                    "rejects a valid uncompressed snapshot padded with more "
                    "leading JSON whitespace than that -- so such a member is "
                    "lost upstream of the bundle path and never reaches the "
                    "sniff at all. Pre-existing and not introduced by the "
                    "operand-shape fix; closing it means widening a "
                    "tool-wide input classifier, which governs far more than "
                    "bundle members. What is enforced instead is the "
                    "*relationship*: everything discovery accepts, the bundle "
                    "sniff accepts too "
                    "(TestDiscoveryAndBundleSniffAgree), so the two cannot "
                    "drift into the dangerous direction."
                ),
                reference="PR #1269 (Codex review)",
                canary_test="tests/test_bundle_stored_file_members.py",  # TestUpstreamDiscoveryProbeBoundCanary
            ),
        ),
        axes={
            "operand_shape": ("package_directory", "snapshot_file"),
            "compression": ("none", "gzip", "zstd", "auto"),
            "envelope": ("flat", "sectioned"),
        },
    ),
    BugClass(
        id="comparability.incidental_ordering_treated_as_contract",
        invariant=(
            "A comparability refusal must rest on evidence that the two "
            "sides are genuinely not comparable — not on an extraction fact "
            "whose value the user never chose. Declared-header ORDER is "
            "load-bearing only where it changes an existing header's parse "
            "context; a growth that preserves every existing header's "
            "relative order and adds only headers the scope fingerprint "
            "independently confirms as new is an ADDITION, and an addition "
            "bounds a comparison (a dimension-scoped assurance reduction, "
            "recorded with its reason) instead of refusing to produce any "
            "verdict. The outcome must not depend on WHERE the added header "
            "sorts, and the invariant is stated over every insertion "
            "position and every permutation — not over the one reported "
            "header name."
        ),
        # Real integration evidence: a project whose public headers are
        # discovered by a sorted directory sweep added one header named
        # `json.h`, which sorts between `data.h` and `log.h`. The only
        # waiver was a strict TRAILING append, so an ordinary public-header
        # addition produced `profile_fingerprint mismatch; differing fields:
        # header_sequence`, no verdict, and no findings on ANY axis — the
        # binary's own exported-symbol conclusions included, which a header
        # insertion cannot touch at all.
        fixed_by=(1274,),
        seed_tests=("tests/test_comparability_gate_header_insertion.py",),
        # The seed tests call `check_contracts_comparable`/`checker.compare`
        # directly, never through Click or `abicheck.service`.
        public_surfaces=(),
        axes={
            "insertion_position": ("first", "interior", "trailing"),
            "sequence_shape": (
                "single-insertion",
                "multi-insertion",
                "reorder",
                "shrink",
                "duplicate",
                "sentinel",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The DISPOSITION is corrected, not the extraction fact "
                    "underneath it: the aggregate driver TU still parses "
                    "declared headers sequentially, so an insertion really "
                    "does change later headers' preprocessing context and "
                    "the bounded outcome is an honest one. Parsing each "
                    "declared header in an independently scoped TU would "
                    "make declared order non-load-bearing outright — even a "
                    "reorder would then be comparable at full assurance — "
                    "but that is a dumper change with a real per-header TU "
                    "cost, not attempted here. A genuine reorder of "
                    "existing headers therefore remains a hard refusal."
                ),
                reference="abicheck/comparability_sequences.py",
                canary_test=None,
            ),
            KnownGap(
                description=(
                    "A bounded run's findings are still scored by ordinary "
                    "policy: `comparability_assurance` marks declaration and "
                    "layout unverified, but no per-finding assurance wiring "
                    "exists (ADR-050 E-S2 defers it), so if an inserted "
                    "header's macros/pragmas did hide a later declaration, "
                    "the resulting finding would score a normal verdict. It "
                    "cannot become a silent clean pass -- the reduction is in "
                    "`coverage_warnings`, `comparability_assurance`, and "
                    "`AnalysisAssurance.status == 'partial'`, which "
                    "`assurance.require_complete` gates on -- and the "
                    "pre-change behavior (a hard refusal) failed such a run "
                    "too, so no run that previously passed can now fail. "
                    "Closing it needs per-finding dimension attribution."
                ),
                reference="PR #1274 (Codex review, second P1)",
                canary_test=None,
            ),
        ),
    ),
)
