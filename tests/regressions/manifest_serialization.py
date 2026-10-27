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

"""Bug classes about turning a model into its persisted or projected form.

A sibling of `manifest_evidence.py`/`manifest_guards.py`/
`manifest_report.py`/`manifest_tool_surface.py` (see `manifest.py` for what
this registry is and is not). The classes here are about the *encode*
direction specifically: a walk over a model tree that produces bytes, a dict,
or a digest.

The two share a root cause worth naming once: in both, a generic tree-walking
primitive got a *special* value wrong while handling the general case
correctly -- a `str`-subclass `Enum` silently downcast by a rewrite that
returns a new plain `str`, and a field excluded from a projection by mutating
the source rather than by skipping it. Neither is a wrong computation over
the right input; both are a general walk meeting a case its generality does
not actually cover.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

SERIALIZATION_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="serialization.pure_projection_mutates_its_own_input",
        invariant=(
            "A projection function documented as pure with respect to its "
            "argument -- a serializer, encoder, digest or any other "
            "read-only view -- must never reach its result by *writing to "
            "that argument*, however briefly, and a `try/finally` that "
            "restores the original values does not make it pure. For the "
            "window between the write and the restore the caller's own "
            "object is observably wrong to every other holder of it: a "
            "second serialization, a reader on another thread, or a "
            "callback reached from inside the projection's own walk. "
            "Fields the projection must not descend into are excluded at "
            "the point of the walk (a skip set, a filtered field "
            "iteration), never by clearing them on the source. The "
            "regression test must observe the argument *from inside* the "
            "projection -- a comparison taken before and after the call "
            "cannot fail, because the `finally` has already restored "
            "everything by the time the call returns, which is exactly why "
            "this defect survived in `snapshot_to_dict()` unnoticed."
        ),
        fixed_by=(1323,),
        seed_tests=("tests/test_snapshot_encode_single_pass.py",),
    ),
    BugClass(
        id="serialization.str_enum_downcast_via_generic_rewrite",
        invariant=(
            "A generic tree-walking string-collect/rewrite primitive "
            "(`isinstance(value, str)`-gated) must never treat a "
            "`str`-subclass `Enum` field (e.g. `ParamKind(str, Enum)`) as "
            "ordinary rewritable free text -- even though isinstance() is "
            "true for it too -- because a real rewrite function (`re.sub`) "
            "returns a genuinely new, plain `str` object even on zero "
            "substitutions, silently downcasting the field's type while "
            "leaving its string value unchanged. The walk must exclude any "
            "`str`-subclass `Enum` member regardless of that enum's own "
            "vocabulary, not special-case the one field that happened to "
            "crash a caller."
        ),
        fixed_by=(985,),
        seed_tests=(
            "tests/test_param_kind_enum_identity.py",
            "tests/test_str_enum_downcast_walk.py",
        ),
    ),
    BugClass(
        id="serialization.persisted_field_not_decoded",
        invariant=(
            "Every field a snapshot encoder writes for a persisted fact "
            "dataclass must be read back by its decoder: a hand-written "
            "decoder that names fields one by one silently drops any field "
            "it forgets, and nothing fails, because the default value "
            "(often None) is a legal reading. Here a dropped "
            "ElfSymbol.origin_lib erased every dependency-leak finding on "
            "a cache hit. The test derives the field list from "
            "dataclasses.fields, so a field added later is covered without "
            "editing the test. The same test must also name every member of "
            "an enum or status field, not sample one, and must not normalize "
            "a status away: a comparison that maps every "
            "`AdvancedExtractionStatus` to one value, or excludes a field, "
            "hides the drop it is meant to catch. Each lossy case is a named "
            "entry with its reason, and each entry must still be lossy."
        ),
        fixed_by=(1442,),
        seed_tests=(
            "tests/test_elf_symbol_roundtrip_and_origin.py",
            "tests/test_family_f2_route_parity.py",
            "tests/unit/storage/test_snapshot_field_roundtrip.py",
            "tests/unit/storage/test_source_abi_surface_roundtrip.py",
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "A stored snapshot does not keep the advanced-DWARF "
                    "extraction status: the encoder drops it, so NOT_RUN, "
                    "COMPLETED and FAILED all read back as UNRECORDED and a "
                    "failed parse falls back to the payload inference. "
                    "Pinned by the KNOWN_LOSSY entries named "
                    "`AdvancedDwarfMetadata.extraction[...]` and "
                    "`Declarations.debug_advanced`; persisting it needs "
                    "ADR-080's decision."
                ),
                reference=(
                    "docs/contribute/known-gaps/"
                    "advanced-facts-collected-infers-advanced-dwarf-extraction-ran.md"
                ),
            ),
            KnownGap(
                description=(
                    "An ownership_fact is persisted only as a PRESENT decision "
                    "inside an extraction_scope. Every other status reads back "
                    "as None, and a PRESENT fact set without a scope is dropped. "
                    "Pinned by the KNOWN_LOSSY entries named "
                    "`ownership_fact[...]`; ownership_of() treats all of them as "
                    "unknown, so no verdict changes, but the Fact is not "
                    "round-tripped. Persisting the status is a schema change."
                ),
                reference=(
                    "docs/contribute/known-gaps/"
                    "ownership-fact-persists-only-a-present-decision.md"
                ),
            ),
        ),
    ),
    BugClass(
        id="serialization.interpreter_dependent_str_subclass_token",
        invariant=(
            "Every str-valued enum member must serialize to its declared "
            "value on every supported interpreter. A `(str, Enum)` mixin "
            "relies on json reading the underlying str buffer; CPython 3.15 "
            "encodes str subclasses through str(), which for such a mixin is "
            "'Cls.NAME', so 3.15-written snapshots, baselines and caches no "
            "interpreter could load. A same-interpreter round trip cannot "
            "see this, so the oracle is the declared value: for every enum "
            "in the package, str(m), format(m) and the json token equal "
            "m.value, which holds on 3.11-3.15 only for enum.StrEnum."
        ),
        fixed_by=(1552,),
        seed_tests=("tests/test_str_enum_serialized_token.py",),
        axes={
            "rendering": ("str", "format", "json"),
            "interpreter": ("3.11", "3.13", "3.15", "3.15t"),
        },
    ),
    BugClass(
        id="storage.short_decode_mistaken_for_complete_decode",
        invariant=(
            "A bounded read over a compressed/streamed source must never "
            'treat "the decoder returned without raising" as "the '
            'decode is complete": a stream cut at an arbitrary raw-byte '
            "boundary yields a *short* result -- commonly zero bytes -- "
            "with no exception at all, so a result shorter than the "
            "requested length is a truncation signal that must escalate "
            "the raw read exactly as a raised exception does, unless the "
            "underlying source is already exhausted. Concretely: "
            "bounded_decoded_prefix(p, n) == read_snapshot_bytes(p)[:n] "
            "for every valid storage envelope whose first n decoded bytes "
            "are reachable within the reader's own raw-input budget, at "
            "every compression ratio; past that budget the answer is None "
            '("no prefix within budget"), never a short value that would '
            "read as the real prefix."
        ),
        fixed_by=(1164,),
        seed_tests=("tests/test_bounded_decoded_prefix_properties.py",),
        public_surfaces=("python-api",),
        axes={
            "algorithm": ("zstd", "gzip"),
            "compression_ratio": ("low", "high"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    'The same "short read is not EOF" shape exists in '
                    "every other bounded/streamed reader in the tree "
                    "(`workflows/bundle_compare_operand.py`'s own larger-"
                    "window probe, `workflows/dump/cache/`'s archive reader, "
                    "the DWARF/PE section readers). Only the snapshot "
                    "storage envelope's prefix primitive has a "
                    "generalized suite; the siblings are untested against "
                    "this invariant. Separately, the bounded reader's own "
                    "guarantee stops at its raw-input cap: an envelope can "
                    "place arbitrarily much stored input before its n-th "
                    "decoded byte (a tiny data frame, a megabyte skippable "
                    "frame, then the payload), and reading far enough to "
                    "decode it is the whole-file decompression the bounded "
                    "reader exists to avoid. That case answers None by "
                    "design and is pinned by a test, but it is a real "
                    "narrowing of the invariant above, not a closed case."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-7",
            ),
        ),
    ),
    BugClass(
        id="storage.cross_generation_spelling_drift",
        invariant=(
            "A snapshot extracted by an older extractor generation, compared "
            "against a fresh dump of identical source, reports no change and "
            "carries a visible generation notice: the generation is stamped "
            "on every single-file snapshot (absent reads as unstated, never "
            "current), drift is decided by the one reader-compatibility "
            "owner, and every extractor generation records the stored type "
            "spellings it changed (or that it changed none), which the load "
            "path migrates at every position a type spelling is stored."
        ),
        # #1698 introduced the AutoType -> auto spelling this class escaped through.
        fixed_by=(1698,),
        seed_tests=(
            "tests/test_snapshot_generation_migration.py",
            "tests/test_field_shapes.py",
            "tests/test_family_f1_evidence_ablation.py",
            "tests/test_generation_corpus.py",
            "tests/test_generation_drift_demotion.py",
            "tests/test_generation_corpus_gate.py",
        ),
    ),
)
