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
            "entry with its reason, and each entry must still be lossy. "
            "The same holds for the stored section-version "
            "inventory (`section_schema_versions`): a version the writer "
            "records must be read back against this build's registry, or a "
            "too-new section (`binary` at 99 on a v1 section) decodes "
            "silently while the top-level schema_version is refused. A "
            "field a stored snapshot must carry to reproduce a live result "
            "is persisted, never declared runtime-only: the branch scan that "
            "attributes an undeclared export to its guard macro was "
            "runtime-only until schema v67, so the same two libraries read "
            "live and read stored disagreed on the census. The encoder's "
            "runtime-only declaration is the single list the round-trip "
            "test reads, and a recorded spelling generation is written "
            "whether or not a fact-generation stamp was carried."
        ),
        fixed_by=(1442,),
        seed_tests=(
            "tests/test_elf_symbol_roundtrip_and_origin.py",
            "tests/test_family_f2_route_parity.py",
            "tests/test_advanced_extraction_status.py",
            "tests/test_live_stored_advanced_dwarf_parity.py",
            "tests/test_live_stored_census_parity.py",
            "tests/test_preprocessor_hidden_census_integration.py",
            "tests/unit/storage/test_snapshot_field_roundtrip.py",
            "tests/unit/storage/test_source_abi_surface_roundtrip.py",
            "tests/test_storage_roundtrip_sweep.py",
            "tests/unit/storage/test_section_schema_version_gate.py",
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
    BugClass(
        id="storage.republish_overwrites_live_ref",
        invariant=(
            "Publishing a ProjectSnapshot package never rewrites a file the "
            "live manifest.json names: ref documents are written once to "
            "digest-named paths, manifest.json is replaced atomically after "
            "every section object and ref is durable and verified, and "
            "writers to one root are serialized. So for every failure point "
            "(a failed object check of any section owner, an interruption at "
            "any ref write or at the manifest write) and for a successful "
            "republish of changed content under the same ids, the previously "
            "published package reads back exactly and every pre-existing "
            "file other than manifest.json is byte-identical."
        ),
        # #1858 records the defect (ADR-062's 2026-10-30 amendment, D17).
        fixed_by=(1858,),
        seed_tests=("tests/test_package_immutable_publication.py",),
        axes={
            "failure_point": ("object-check", "ref-write", "manifest-write"),
            "section_owner": ("artifact", "variant", "project"),
            "writers": ("one", "concurrent"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Superseded refs and section objects are never collected, "
                    "and without fcntl (Windows) or in reference mode writers "
                    "are not serialized (they still cannot mix packages)."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="storage.digest_answers_the_wrong_question",
        invariant=(
            "Each stored-content digest answers exactly one ADR-062 D16 "
            "question. The `evidence` digest is equal for every pair "
            "`compare` reports no change for because only capture metadata "
            "(created_at, source path/mtime, CI build id) or the producer "
            "order of an identity-matched record list differs, and differs "
            "for a real change, including a reordering of a semantically "
            "ordered list (parameters, fields, enum members, DT_NEEDED) or of "
            "records sharing one identity. The `publication` digest keeps "
            "its exact meaning, and a consumer asking the evidence question "
            "never answers it from the publication digest's inequality."
        ),
        # ADR-062's 2026-10-30 amendment (#1858) records the probes (D16).
        fixed_by=(1858,),
        seed_tests=(
            "tests/unit/storage/test_evidence_digest.py",
            "tests/test_snapshot_digest_prefilter.py",
            "tests/test_family_f3_identity.py",
        ),
        public_surfaces=("cli",),
        axes={
            "difference": (
                "volatile-capture",
                "unordered-permutation",
                "ordered-reorder",
                "duplicate-identity-order",
                "real-change",
            ),
            "digest_source": ("computed", "persisted"),
        },
    ),
    BugClass(
        id="storage.wire_extension_dropped_or_refused",
        invariant=(
            "A stored snapshot's data a reader does not understand is never "
            "silently dropped and never refuses data it could still compare: "
            "an unknown optional section, header extension or record field "
            "is written back unchanged on re-save and is inside the evidence "
            "and publication digests; a section carrying an unknown "
            "must-understand name, a newer section_schema_version, or a "
            "failed availability entry resolves exactly the analyses that "
            "read it to not comparable (never to an empty section, a removal "
            "or a clean pass) while every other analysis still runs."
        ),
        # ADR-062's 2026-10-30 amendment (#1858) records the probes (D14/D15).
        fixed_by=(1858,),
        seed_tests=(
            "tests/unit/storage/test_extension_preservation.py",
            "tests/test_section_readers_contract.py",
            "tests/test_cli_compare_must_understand.py",
            "tests/unit/storage/test_wire_contract.py",
            "tests/test_family_f1_evidence_ablation.py",
        ),
        public_surfaces=("cli",),
        axes={
            "carrier": (
                "unknown-section",
                "header-extensions",
                "record-field",
                "must-understand",
                "newer-section-version",
                "failed-section",
            ),
            "level": ("record", "section", "document", "manifest"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Unknown keys nested below a record's top level are still "
                    "dropped on re-save, and a directory package refuses an "
                    "unknown section-level must-understand name instead of "
                    "narrowing it."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
)
