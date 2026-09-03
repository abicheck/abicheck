# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Contract tests for ADR-062 D5's canonical encoding.

The digest invariant, stated once: `semantic_digest` is invariant under
mapping key order, set iteration order, and pretty-printing, and is *not*
invariant under sequence order. Everything here tests one half of that.

D2's version axes were tested here too until this file crossed the 1200-line
test cap; they live in `test_versioning.py` now. The split followed the line
this docstring itself had already drawn by naming two subjects.
"""

from __future__ import annotations

import array
import hashlib
import itertools
import json
import os

import pytest
from hypothesis import given, strategies as st

from abicheck.storage.canonical import (
    CAPTURE_METADATA_KEY,
    canonical_form,
    canonical_json,
    raw_digest,
    semantic_digest,
    strip_capture_metadata,
)

_json_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**9), max_value=10**9),
    st.text(max_size=16),
)
_json_values = st.recursive(
    _json_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=8), children, max_size=4),
    ),
    max_leaves=12,
)


class TestDigestIgnoresIncidentalOrder:
    @given(_json_values)
    def test_mapping_key_order_never_changes_a_digest(self, value: object) -> None:
        shuffled = json.loads(json.dumps(value, sort_keys=True))

        assert semantic_digest(value) == semantic_digest(shuffled)

    def test_a_mapping_built_in_two_orders_hashes_the_same(self) -> None:
        forward = {"a": 1, "b": 2, "c": 3}
        backward = {"c": 3, "b": 2, "a": 1}

        assert semantic_digest(forward) == semantic_digest(backward)

    def test_set_iteration_order_never_changes_a_digest(self) -> None:
        assert semantic_digest({"tags": {"x", "y", "z"}}) == semantic_digest(
            {"tags": {"z", "y", "x"}}
        )

    def test_a_set_of_mixed_types_still_orders_deterministically(self) -> None:
        """Sorting by canonical text, not by value.

        A direct `sorted()` raises on a set holding both ints and strings —
        which a real facts payload can easily produce.
        """
        digest = semantic_digest({"mixed": {1, "1", True}})

        assert semantic_digest({"mixed": {"1", True, 1}}) == digest

    def test_equal_sets_hash_equally_even_across_the_bool_int_collapse(self) -> None:
        """`{1} == {True}` in Python, so their digests must agree too.

        Which spelling survives set construction depends only on insertion
        order, so emitting `true` for one and `1` for the other would
        reintroduce an incidental-order dependence — just hidden inside
        `set.__hash__` rather than in a producer's traversal. The
        distinction is unrecoverable here by construction; agreement is the
        only available answer.
        """
        assert {1} == {True}
        assert semantic_digest({1}) == semantic_digest({True})
        # Outside a set, the distinction is real and is preserved.
        assert semantic_digest([1]) != semantic_digest([True])

    @given(st.integers(min_value=0, max_value=4))
    def test_pretty_printing_never_changes_a_digest(self, indent: int) -> None:
        value = {"b": [1, 2], "a": {"nested": True}}

        canonical_json(value, indent=indent or None)

        assert semantic_digest(value) == semantic_digest(
            json.loads(canonical_json(value, indent=indent or None))
        )


class TestDigestRespectsRealOrder:
    def test_sequence_order_does_change_a_digest(self) -> None:
        """A sequence is the shape that *means* order is significant.

        This is the other half of the `BundleFacts` lesson: template
        arguments must be an array of explicit entries, so that sorting
        mappings elsewhere is safe.
        """
        assert semantic_digest({"args": [1, 2]}) != semantic_digest({"args": [2, 1]})

    def test_explicit_ordered_entries_survive_canonicalization(self) -> None:
        template_args = [
            {"parameter": "Precision", "value": "double"},
            {"parameter": "Method", "value": "defaultDense"},
        ]

        assert canonical_form(template_args) == template_args
        assert semantic_digest(template_args) != semantic_digest(
            list(reversed(template_args))
        )

    def test_the_insertion_ordered_mapping_antipattern_is_lossy(self) -> None:
        """Why the array shape is required, demonstrated rather than asserted.

        A mapping that encodes argument order by insertion collapses to one
        digest under canonicalization — so a format relying on it cannot tell
        `<double, defaultDense>` from `<defaultDense, double>`.
        """
        as_map = {"Precision": "double", "Method": "defaultDense"}
        reordered_map = {"Method": "defaultDense", "Precision": "double"}

        assert semantic_digest(as_map) == semantic_digest(reordered_map)


class TestCaptureMetadata:
    """One reserved slot at the root, not a set of names at any depth."""

    def test_the_reserved_root_slot_is_excluded_from_the_digest(self) -> None:
        assert semantic_digest(
            {"facts": [1], CAPTURE_METADATA_KEY: {"hostname": "runner-1"}}
        ) == semantic_digest(
            {"facts": [1], CAPTURE_METADATA_KEY: {"hostname": "runner-2"}}
        )

    def test_it_is_excluded_only_at_the_root(self) -> None:
        """Position, not spelling, is what makes the exclusion sound."""
        nested_a = {"entities": {CAPTURE_METADATA_KEY: {"type": "int"}}}
        nested_b = {"entities": {}}

        assert semantic_digest(nested_a) != semantic_digest(nested_b)

    def test_strip_removes_only_the_root_slot(self) -> None:
        payload = {
            CAPTURE_METADATA_KEY: {"pid": 1},
            "entities": {CAPTURE_METADATA_KEY: {"real": 1}},
        }

        assert strip_capture_metadata(payload) == {
            "entities": {CAPTURE_METADATA_KEY: {"real": 1}}
        }

    def test_a_non_mapping_root_is_returned_unchanged(self) -> None:
        assert strip_capture_metadata([1, 2]) == [1, 2]

    def test_the_stored_document_keeps_its_capture_metadata(self) -> None:
        """Excluded from *hashing*, not from what is written."""
        payload = {CAPTURE_METADATA_KEY: {"hostname": "h"}, "x": 1}

        assert CAPTURE_METADATA_KEY in canonical_json(payload)
        assert CAPTURE_METADATA_KEY not in canonical_json(
            payload, drop_capture_metadata=True
        )


class TestNoContentKeyIsStrippedByName:
    """Codex review, twice. The name-based strip was the wrong mechanism.

    `host` was removed after it collapsed `{"host": "linux"}`,
    `{"host": "windows"}` and `{}` to one digest. Removing that one name did
    not fix the class — the next round found `pid` (an entirely ordinary C
    struct field) and `working_directory` (a real build input) doing the same
    thing. Each fix drew the next instance, which is the signal to change the
    mechanism rather than keep editing the list.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "host",
            "hostname",
            "pid",
            "created_at",
            "captured_at",
            "generated_at",
            "duration_seconds",
            "elapsed_seconds",
            "tmpdir",
            "scratch_dir",
            "working_directory",
            "wall_clock_seconds",
        ],
    )
    def test_every_previously_stripped_name_is_now_content(self, name: str) -> None:
        assert semantic_digest({name: "a"}) != semantic_digest({name: "b"})
        assert semantic_digest({name: "a"}) != semantic_digest({})

    def test_the_reported_pid_entity_survives(self) -> None:
        """The literal counterexample from review."""
        assert semantic_digest(
            {"entities": {"pid": {"type": "int"}}}
        ) != semantic_digest({"entities": {}})

    def test_a_working_directory_build_input_survives(self) -> None:
        assert semantic_digest(
            {"build": {"working_directory": "/a"}}
        ) != semantic_digest({"build": {"working_directory": "/b"}})


class TestNumberNormalization:
    @pytest.mark.parametrize(
        ("left", "right"),
        [(0.0, -0.0), (2.0, 2), (1e3, 1000)],
    )
    def test_equal_numbers_encode_identically(self, left: float, right: float) -> None:
        assert semantic_digest(left) == semantic_digest(right)

    def test_a_genuinely_fractional_value_is_preserved(self) -> None:
        assert canonical_form(1.5) == 1.5
        assert semantic_digest(1.5) != semantic_digest(1.25)

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_floats_are_refused(self, value: float) -> None:
        """`json` emits these as bare literals that are not valid JSON.

        A document containing one is unreadable by a conforming parser, so it
        must not be written at all — failing at the boundary beats writing a
        package no other tool can read.
        """
        with pytest.raises(ValueError, match="non-finite"):
            canonical_form(value)


class TestUnsupportedTypesAreRefused:
    def test_an_arbitrary_object_is_a_type_error(self) -> None:
        """No `str()` fallback.

        A silent coercion would let an object whose `repr` embeds a memory
        address into the hash domain, so the digest of identical content
        would differ run to run — the one thing a content-addressed store
        cannot tolerate.
        """

        class Opaque:
            pass

        with pytest.raises(TypeError, match="no canonical storage form"):
            canonical_form({"x": Opaque()})

    def test_bytes_are_refused_rather_than_guessed_at(self) -> None:
        """`bytes` is a `Sequence`, so it needs an explicit guard.

        Without one it falls through to the sequence branch and encodes as a
        list of integers — a silent, lossy reinterpretation rather than an
        error, and one that would then hash as if it were content.
        """
        with pytest.raises(TypeError, match="bytes has no canonical storage form"):
            canonical_form(b"raw")

        with pytest.raises(TypeError):
            canonical_form({"payload": bytearray(b"raw")})


class TestEveryBinaryBufferIsRefused:
    """The guard is the buffer protocol, not a list of types.

    An enumerated guard is only as complete as its list: `memoryview` is a
    `Sequence`, so it fell through and encoded as `[114, 97, 119]`, taking
    the same digest as a genuine integer list (Codex review). `array.array`
    and `mmap.mmap` have the same shape, so the next one would have been the
    third instance of one bug.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(b"raw", id="bytes"),
            pytest.param(bytearray(b"raw"), id="bytearray"),
            pytest.param(memoryview(b"raw"), id="memoryview"),
            pytest.param(array.array("i", [1, 2]), id="array"),
        ],
    )
    def test_a_binary_buffer_is_refused_at_every_entry_point(
        self, payload: object
    ) -> None:
        """Both public entry points route through `canonical_form`.

        Checked at each rather than only at the shared helper: it is the
        entry points that a caller reaches, and the routing is an internal
        detail that a later change could quietly alter.
        """
        for call in (canonical_form, canonical_json, semantic_digest):
            with pytest.raises(TypeError, match="no canonical storage form"):
                call({"payload": payload})

    def test_a_refused_buffer_names_its_own_type(self) -> None:
        """The message says what was passed, not always "bytes".

        A caller who reached this with a `memoryview` is told that, rather
        than being sent looking for a `bytes` they never passed.
        """
        with pytest.raises(TypeError, match="memoryview has no canonical"):
            canonical_form(memoryview(b"raw"))

    def test_a_buffer_no_longer_collides_with_an_integer_list(self) -> None:
        """The consequence, stated directly rather than through the guard.

        This is what the defect actually was: not "a type was accepted", but
        that an inline binary payload took the address of unrelated logical
        content and would deduplicate against it.
        """
        with pytest.raises(TypeError):
            semantic_digest({"k": memoryview(b"raw")})

        assert semantic_digest({"k": [114, 97, 119]}) == semantic_digest(
            {"k": [114, 97, 119]}
        )

    def test_ordinary_content_is_untouched(self) -> None:
        """The control: the guard must not widen past binary payloads.

        `str` exposes no buffer, and a list of integers is exactly the shape
        a `memoryview` was being confused with — both must still pass.
        """
        assert canonical_form({"s": "hi", "n": [114, 97, 119]}) == {
            "n": [114, 97, 119],
            "s": "hi",
        }


class TestCanonicalFormBasics:
    @given(_json_values)
    def test_canonical_form_is_idempotent(self, value: object) -> None:
        once = canonical_form(value)

        assert canonical_form(once) == once

    @given(_json_values)
    def test_canonical_json_parses_back_to_canonical_form(self, value: object) -> None:
        assert json.loads(canonical_json(value)) == canonical_form(value)

    def test_a_digest_names_its_algorithm(self) -> None:
        """So a stored digest never leaves a reader to assume sha256."""
        assert semantic_digest({"a": 1}).startswith("sha256:")
        assert semantic_digest({"a": 1}, algorithm="sha512").startswith("sha512:")

    def test_non_string_mapping_keys_are_refused(self) -> None:
        """Codex review. The previous version of this test pinned the bug.

        It asserted the result was *one of* `{"1": "a"}` / `{"1": "b"}` —
        which reads as a deliberate normalization choice but was really a
        silent loss: `{1: "a", "1": "b"}` has two entries and the digest
        matched a document that only ever had one. A test written to accept
        whichever value survived made a real gap look settled, which is the
        same trap the root `AGENTS.md` records for the forced-include work.
        """
        with pytest.raises(TypeError, match="not str"):
            canonical_form({1: "a", "1": "b"})

    def test_a_key_collision_cannot_silently_drop_an_entry(self) -> None:
        with pytest.raises(TypeError):
            canonical_form({1: "a"})

    def test_unorderable_values_under_colliding_keys_do_not_crash_sorting(
        self,
    ) -> None:
        """Sorting pairs fell through to comparing values when keys tied.

        `{1: {}, "1": []}` raised `TypeError: '<' not supported between
        instances of 'list' and 'dict'` from inside a digest call. Sorting by
        key alone makes value orderability irrelevant; the non-string keys are
        refused first, and the error names the key rather than the comparison.
        """
        with pytest.raises(TypeError, match="not str"):
            canonical_form({1: {}, "1": []})

    def test_string_keyed_mappings_with_unorderable_values_are_fine(self) -> None:
        assert canonical_form({"b": [], "a": {}}) == {"a": {}, "b": []}


class TestSurrogateEscapedContentIsHashable:
    """Codex review: a real POSIX path could make a package unaddressable.

    A filesystem path carrying a non-UTF-8 byte decodes through
    `surrogateescape` into a lone surrogate — `os.fsdecode(b"caf\\xe9")` is
    `"caf\\udce9"` — and encoding that to UTF-8 raises `UnicodeEncodeError`.
    The failure was asymmetric, which is what made it worse than a refusal:
    `canonical_json` accepted the value, so a document could be produced that
    `semantic_digest` could not address.
    """

    #: A real path shape, not a synthetic code point: exactly what `os` hands
    #: back for a latin-1 byte in a POSIX filename.
    #:
    #: Spelled literally rather than as `os.fsdecode(b"/src/caf\xe9.h")`.
    #: `fsdecode` uses the *host's* filesystem encoding and error handler, and
    #: on Windows that is UTF-8 with `surrogatepass`, which cannot decode a
    #: bare `\xe9` at all — so the call raised `UnicodeDecodeError` at class-
    #: body time and took the whole module's collection with it, on a platform
    #: this branch's CI had not yet completed a run on (CodeRabbit review,
    #: filed as portability; the import break is the sharper half). Under a
    #: latin-1 locale it fails the other way, returning `"/src/café.h"` with
    #: no lone surrogate, which quietly makes these tests assert nothing.
    #:
    #: `test_the_literal_is_what_fsdecode_produces` keeps the literal honest
    #: where the host can produce it.
    SURROGATE_PATH = "/src/caf\udce9.h"

    def test_a_surrogate_escaped_path_can_be_digested(self) -> None:
        assert semantic_digest({"path": self.SURROGATE_PATH}).startswith("sha256:")

    def test_the_digest_is_stable_across_calls(self) -> None:
        again = "/src/caf\udce9.h"

        assert semantic_digest({"path": self.SURROGATE_PATH}) == semantic_digest(
            {"path": again}
        )

    def test_the_literal_is_what_fsdecode_produces(self) -> None:
        """The fixture claims to be a real path shape; this checks the claim.

        Skipped where the host cannot produce one — that is the whole reason
        the fixture is a literal — so the check runs wherever it is meaningful
        and never decides the module's importability.
        """
        try:
            decoded = os.fsdecode(b"/src/caf\xe9.h")
        except UnicodeDecodeError:  # pragma: no cover - platform-dependent
            pytest.skip("this host's filesystem encoding cannot produce one")
        if "\udce9" not in decoded:  # pragma: no cover - platform-dependent
            pytest.skip(f"this host decodes the byte as {decoded!r}")

        assert decoded == self.SURROGATE_PATH

    def test_it_does_not_collide_with_the_ascii_spelling(self) -> None:
        """Escaping must stay injective — the whole point of a content address."""
        assert semantic_digest({"path": self.SURROGATE_PATH}) != semantic_digest(
            {"path": "/src/cafe.h"}
        )

    @pytest.mark.parametrize(
        "text",
        [
            "café",  # ordinary non-ASCII
            "日本語",  # non-latin
            "😀",  # non-BMP, escapes as a surrogate pair
            "\udcff\udcfe",  # two lone surrogates, as `os.fsdecode(b"\xff\xfe")`
            "a\udce9b\udcffc",  # surrogates interleaved with ASCII
        ],
    )
    def test_every_string_shape_is_hashable_and_distinct(self, text: str) -> None:
        digest = semantic_digest({"k": text})

        assert digest.startswith("sha256:")
        assert digest != semantic_digest({"k": "placeholder"})

    def test_canonical_json_still_accepts_it(self) -> None:
        """Pinning the asymmetry rather than pretending it is gone.

        The stored document deliberately keeps `ensure_ascii=False` for
        readability, so this succeeds while a UTF-8 encode of its output would
        not. The digest path is the one that had to be made total; a Phase 1
        writer's handling of such a path is its own explicit decision.
        """
        rendered = canonical_json({"path": self.SURROGATE_PATH})

        assert "caf" in rendered
        with pytest.raises(UnicodeEncodeError):
            rendered.encode("utf-8")


class TestNestedBooleansInSetMembersAgree:
    """Codex review: the bool/int collapse stopped at the member's top level.

    Python considers `{(True,)}` and `{(1,)}` equal sets — which tuple survives
    construction depends only on which was inserted first — but they
    canonicalized to `[[true]]` and `[[1]]` and received different digests.
    That is exactly the defect the top-level collapse was written to fix, one
    level down: the fix had been scoped to the shape that was demonstrated
    rather than to the rule.
    """

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            pytest.param({(True,)}, {(1,)}, id="tuple"),
            pytest.param({((True,),)}, {((1,),)}, id="nested-tuple"),
            pytest.param({frozenset({True})}, {frozenset({1})}, id="frozenset"),
            pytest.param({(False,)}, {(0,)}, id="false-and-zero"),
            pytest.param({(1, (False, "x"))}, {(True, (0, "x"))}, id="mixed-depths"),
        ],
    )
    def test_equal_sets_receive_equal_digests(self, left: set, right: set) -> None:
        assert left == right, "fixture must be equal sets or this proves nothing"

        assert semantic_digest({"s": left}) == semantic_digest({"s": right})

    def test_a_boolean_outside_a_set_is_still_content(self) -> None:
        """The collapse must stay scoped to where equality forces it.

        `{"x": True}` and `{"x": 1}` are genuinely different documents; it is
        set *membership* that makes the distinction unrecoverable, so it is
        only there that agreeing is the sole option left.
        """
        assert semantic_digest({"x": True}) != semantic_digest({"x": 1})
        assert semantic_digest({"x": [True]}) != semantic_digest({"x": [1]})

    def test_distinct_sets_still_differ(self) -> None:
        """Collapsing must not merge sets that are not equal."""
        assert semantic_digest({"s": {(1,)}}) != semantic_digest({"s": {(2,)}})
        assert semantic_digest({"s": {(1, 2)}}) != semantic_digest({"s": {(1,)}})


class TestExtendableOutputAlgorithmsAreRefused:
    """Codex review: `hashlib.new` accepts SHAKE, `hexdigest()` does not.

    SHAKE is an extendable-output function, so a caller selecting one got a
    bare `TypeError: hexdigest() missing required argument 'length'` from
    inside a digest call. `algorithm` is public and exists so a future
    algorithm change is expressible, which makes an accepted-but-unusable
    value a real trap rather than a theoretical one.
    """

    @pytest.mark.parametrize("algorithm", ["shake_128", "shake_256"])
    def test_a_variable_length_algorithm_is_refused(self, algorithm: str) -> None:
        with pytest.raises(ValueError, match="extendable-output"):
            semantic_digest({"a": 1}, algorithm=algorithm)

    @pytest.mark.parametrize(
        "algorithm", ["sha256", "sha512", "sha3_256", "blake2b", "blake2s"]
    )
    def test_fixed_length_algorithms_still_work(self, algorithm: str) -> None:
        """Detection is by digest size, not an allowlist of names.

        An allowlist would also refuse a future fixed-length algorithm, which
        is the opposite of what this parameter is for — so every fixed-length
        algorithm hashlib offers must keep working, not just sha256.
        """
        digest = semantic_digest({"a": 1}, algorithm=algorithm)

        assert digest.startswith(f"{algorithm}:")
        assert len(digest.split(":", 1)[1]) > 0

    def test_an_unknown_algorithm_still_reports_itself(self) -> None:
        """The pre-existing error must not be swallowed by the new guard.

        The exception *type* is the contract; the message is not. `hashlib`
        raises `ValueError` for an algorithm it cannot provide, but the
        wording varies with the Python version and with which OpenSSL
        provider is available, so matching it would pin an upstream string
        rather than this function's behaviour (CodeRabbit review).
        """
        with pytest.raises(ValueError):
            semantic_digest({"a": 1}, algorithm="definitely-not-a-hash")

    def test_the_digest_names_the_algorithm_that_produced_it(self) -> None:
        assert semantic_digest({"a": 1}, algorithm="sha512").split(":")[0] == "sha512"


class TestTheDigestPrefixIsTheCanonicalAlgorithmName:
    """Codex review: an alias gave one object several content addresses.

    `hashlib` accepts `SHA256`, `sha-256` and friends, and preserving the
    caller's spelling produced addresses whose hex halves were identical but
    whose prefixes differed — defeating deduplication, and emitting prefixes a
    reader has no reason to recognize. The address is a property of the
    content, so nothing incidental to the caller may reach it.
    """

    @pytest.mark.parametrize("alias", ["SHA256", "sha-256", "SHA-256"])
    def test_aliases_produce_one_address(self, alias: str) -> None:
        assert semantic_digest({"x": 1}, algorithm=alias) == semantic_digest(
            {"x": 1}, algorithm="sha256"
        )

    @pytest.mark.parametrize(
        ("alias", "canonical"),
        [("SHA256", "sha256"), ("SHA-512", "sha512"), ("sha-256", "sha256")],
    )
    def test_the_prefix_is_normalized(self, alias: str, canonical: str) -> None:
        assert semantic_digest({"x": 1}, algorithm=alias).split(":")[0] == canonical

    def test_distinct_algorithms_still_produce_distinct_addresses(self) -> None:
        """Normalizing aliases must not merge genuinely different algorithms."""
        assert semantic_digest({"x": 1}, algorithm="sha256") != semantic_digest(
            {"x": 1}, algorithm="sha512"
        )


class TestTheDigestIsAPureFunctionOfTheDocument:
    """Why collapsing container *type* is correct rather than a collision.

    `{(1, 2)}` and `{frozenset({1, 2})}` are unequal in Python and share a
    `semantic_digest`, which was reported as an ambiguity. They also share a
    `canonical_json` output, byte for byte — JSON has one array type — so
    they are the same stored document, and a content address addresses the
    document (Codex review, declined with evidence).

    Encoding the collection kind to separate them would be worse than
    unnecessary: a reader parsing the document back gets lists and could not
    reconstruct the tag, so a re-derived digest would stop matching the one
    the document was stored under.

    The real invariant is stated here as a property, so this reasoning
    fails loudly if it ever stops holding.
    """

    _SHAPES: list[object] = [
        [1, 2],
        (1, 2),
        {1, 2},
        frozenset({1, 2}),
        [2, 1],
        [],
        (),
        set(),
        frozenset(),
        [[1, 2]],
        {(1, 2)},
        {frozenset({1, 2})},
        {"a": 1},
        [{"a": 1}],
        "12",
        ["1", "2"],
    ]

    def test_the_reported_pair_shares_a_document_not_just_a_digest(self) -> None:
        ordered = {(1, 2)}
        unordered = {frozenset({1, 2})}

        assert ordered != unordered
        assert canonical_json(ordered) == canonical_json(unordered) == "[[1,2]]"
        assert semantic_digest(ordered) == semantic_digest(unordered)

    @pytest.mark.parametrize(
        ("left", "right"), list(itertools.combinations(_SHAPES, 2))
    )
    def test_equal_documents_and_equal_digests_agree_in_both_directions(
        self, left: object, right: object
    ) -> None:
        """The invariant, over every shape rather than the reported pair.

        Both directions matter and fail differently: same document with
        different digests means identical bytes are unaddressable as one
        object; different documents with the same digest is a real
        collision.

        Parametrized rather than looped so one disagreeing pair does not
        stop the rest from being checked in the same run — a loop reports
        the first failure and leaves the remaining pairs unmeasured, which
        for a sweep over shapes is the difference between "one shape is
        wrong" and "an unknown number are" (CodeRabbit review).
        """
        assert (canonical_json(left) == canonical_json(right)) == (
            semantic_digest(left) == semantic_digest(right)
        ), (
            f"{left!r} and {right!r} disagree: document-equal="
            f"{canonical_json(left) == canonical_json(right)}, digest-equal="
            f"{semantic_digest(left) == semantic_digest(right)}"
        )

    @given(_json_values, _json_values)
    def test_the_property_holds_for_generated_documents(
        self, left: object, right: object
    ) -> None:
        assert (canonical_json(left) == canonical_json(right)) == (
            semantic_digest(left) == semantic_digest(right)
        )

    def test_order_still_distinguishes_where_the_document_does(self) -> None:
        """The control: collapse is about *type*, never about content.

        A list whose order differs is a different document and keeps a
        different address — so this is not "arrays all hash alike".
        """
        assert canonical_json([2, 1]) != canonical_json([1, 2])
        assert semantic_digest([2, 1]) != semantic_digest([1, 2])


class TestRawDigest:
    """`raw_digest` -- D7's counterpart to `semantic_digest` for content
    `canonical_form` cannot represent at all (a binary buffer)."""

    def test_is_deterministic(self) -> None:
        payload = b"\x00\x01\xff some bytes \xfe"
        assert raw_digest(payload) == raw_digest(payload)

    def test_different_payloads_get_different_digests(self) -> None:
        assert raw_digest(b"a") != raw_digest(b"b")

    @pytest.mark.parametrize("wrapper", [bytearray, memoryview])
    def test_accepts_bytearray_and_memoryview(self, wrapper: object) -> None:
        payload = b"same content"
        assert raw_digest(wrapper(payload)) == raw_digest(payload)  # type: ignore[operator]

    def test_honors_a_non_default_algorithm(self) -> None:
        payload = b"raw bytes"
        digest = raw_digest(payload, algorithm="sha3_256")
        assert digest.startswith("sha3_256:")
        assert (
            len(digest.removeprefix("sha3_256:")) == hashlib.sha3_256().digest_size * 2
        )

    def test_rejects_a_non_binary_value(self) -> None:
        with pytest.raises(TypeError):
            raw_digest("not bytes")  # type: ignore[arg-type]

    def test_rejects_an_extendable_output_function(self) -> None:
        """Same fixed-digest-size rule `semantic_digest` enforces -- shared
        through `_digest_from_payload`, so the two cannot drift apart."""
        with pytest.raises(ValueError, match="extendable-output"):
            raw_digest(b"x", algorithm="shake_128")

    def test_rejects_a_noncanonical_algorithm_alias(self) -> None:
        """`hashlib.new` accepts `SHA256`, but the emitted address always
        uses the canonical spelling `hashlib` itself reports -- matching
        `semantic_digest`'s own rule so the two functions can't produce two
        different addresses for what a reader would consider one algorithm.
        """
        payload = b"x"
        assert raw_digest(payload, algorithm="SHA256") == raw_digest(
            payload, algorithm="sha256"
        )

    def test_never_strips_anything_shaped_like_capture_metadata(self) -> None:
        """A raw payload has no JSON structure at all, so a byte sequence
        that happens to spell `{"capture": ...}` hashes differently from the
        same bytes with that block actually removed -- unlike
        `semantic_digest`'s root-capture-stripping rule for JSON content,
        there is no document here to inspect a root key of."""
        with_capture = b'{"capture": {"timestamp": "now"}, "x": 1}'
        without_capture = b'{"x": 1}'
        assert raw_digest(with_capture) != raw_digest(without_capture)

    def test_never_collides_with_an_equivalent_json_value(self) -> None:
        """The bug this fix closes: `b"{}"` and `{}` both encode to the
        identical two bytes `b"{}"`, so without a domain separator the two
        functions would compute the same digest for genuinely different
        stored representations -- `InMemoryObjectStore.put()` would then
        keep whichever was stored first and silently discard the other,
        regardless of which one a later caller asked to store (Codex
        review).
        """
        assert raw_digest(b"{}") != semantic_digest({})
        assert raw_digest(b"null") != semantic_digest(None)
        assert raw_digest(b"[]") != semantic_digest([])


class TestAlgorithmPortability:
    """`semantic_digest`/`raw_digest` must agree with `object_relpath`/
    `ObjectRef` (`abicheck.storage.package`) on which algorithms are
    accepted -- both enforce ADR-062 D7's portability rule, and drift
    between the two would let `put()` mint a digest no `ObjectRef` could
    ever be built from (the store producing an unreferenceable object).
    """

    @pytest.mark.parametrize(
        "algorithm",
        sorted(hashlib.algorithms_available - hashlib.algorithms_guaranteed),
    )
    def test_semantic_digest_refuses_an_available_but_unguaranteed_algorithm(
        self, algorithm: str
    ) -> None:
        with pytest.raises(ValueError, match="algorithms_guaranteed"):
            semantic_digest({"x": 1}, algorithm=algorithm)

    @pytest.mark.parametrize(
        "algorithm",
        sorted(hashlib.algorithms_available - hashlib.algorithms_guaranteed),
    )
    def test_raw_digest_refuses_an_available_but_unguaranteed_algorithm(
        self, algorithm: str
    ) -> None:
        with pytest.raises(ValueError, match="algorithms_guaranteed"):
            raw_digest(b"x", algorithm=algorithm)

    def test_a_guaranteed_algorithm_is_still_accepted(self) -> None:
        # The control: this isn't "reject everything", only what isn't
        # guaranteed portable.
        assert semantic_digest({"x": 1}, algorithm="sha3_256").startswith("sha3_256:")


class TestAlreadyCanonicalHelpers:
    """`InMemoryObjectStore.put()`/`get()` (`abicheck.storage.package`) hash
    and copy content they already normalized via `canonical_form` themselves
    -- re-running `canonical_form` on that same output a second time is a
    wasted traversal, since `canonical_form` is idempotent and pure.
    `semantic_digest_of_canonical_form`/`copy_of_canonical_form` exist to
    skip that redundant pass. This is a call-site-independent property of
    `canonical_form` itself (`canonical_form(canonical_form(x)) ==
    canonical_form(x)` for every value in its accepted domain), not specific
    to any one snapshot shape, so it is tested here directly against
    Hypothesis-generated values rather than only through one benchmark.
    """

    @given(_json_values)
    def test_hashing_an_already_canonical_value_matches_semantic_digest(
        self, value: object
    ) -> None:
        from abicheck.storage.canonical import semantic_digest_of_canonical_form

        canonical = canonical_form(value)
        assert semantic_digest_of_canonical_form(
            canonical, algorithm="sha256"
        ) == semantic_digest(value)

    @given(_json_values)
    def test_copying_an_already_canonical_value_matches_canonical_form(
        self, value: object
    ) -> None:
        from abicheck.storage.canonical import copy_of_canonical_form

        canonical = canonical_form(value)
        assert copy_of_canonical_form(canonical) == canonical_form(value)

    def test_the_copy_is_isolated_from_the_original(self) -> None:
        """The property `InMemoryObjectStore.get()` actually depends on:
        mutating the returned copy must never reach the stored object."""
        from abicheck.storage.canonical import copy_of_canonical_form

        original = canonical_form({"a": [1, 2, {"b": 3}]})
        copy = copy_of_canonical_form(original)
        assert copy == original
        assert copy is not original

        copy["a"].append(4)
        copy["a"][2]["b"] = 99
        assert original == {"a": [1, 2, {"b": 3}]}

    def test_a_mapping_and_a_custom_mapping_subtype_still_agree(self) -> None:
        """`canonical_form`'s `dict`/`list` fast path is keyed on exact
        `type()`, ahead of the general `isinstance(..., Mapping)` branch --
        so a non-`dict` `Mapping` (here, `MappingProxyType`) must still take
        the general branch and produce the identical canonical form a plain
        `dict` with the same content does."""
        from types import MappingProxyType

        plain = {"b": 2, "a": 1}
        proxy = MappingProxyType({"b": 2, "a": 1})
        assert canonical_form(proxy) == canonical_form(plain)
        assert semantic_digest(proxy) == semantic_digest(plain)

    def test_a_tuple_and_a_list_still_agree(self) -> None:
        """Same fast-path-vs-general-branch concern, for sequences: a
        `tuple` (not `list`) must still take the general `Sequence` branch
        and canonicalize identically to the equivalent `list`."""
        assert canonical_form((1, "x", {"a": 2})) == canonical_form([1, "x", {"a": 2}])
