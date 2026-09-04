# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Which error kind a malformed stored document raises.

Split out of `test_boundary_parity.py` when that file crossed the 1200-line
test cap. The line follows the subject: its sibling asks *whether* a bad
value is refused at every door, and this asks *how* a refusal is spelled —
`TypeError`/`ValueError` for malformed input, per this package's own
`AGENTS.md`, because that is the pair a caller catches to tell a corrupt
package from a broken reader.
"""

from __future__ import annotations

from typing import Any

import pytest
from adr062_scope import adr062_module_paths

from abicheck.storage.availability import AvailabilityLedger, FactAvailability
from abicheck.storage.availability_status import FactStatus
from abicheck.storage.entity_ids import EntityKind, ObservationKind
from abicheck.storage.identity import (
    EntityId,
    IdentityConflict,
    OccurrenceId,
    OccurrenceSet,
)
from abicheck.storage.versioning import StorageVersions, check_reader_compatibility


class TestMalformedDocumentsRaiseTheDocumentedErrorKinds:
    """A truncated document is malformed input, not a reader crash.

    This package documents the contract in its own `AGENTS.md`: "a caller
    separating 'malformed package' from 'broken reader' catches
    `TypeError`/`ValueError`". `KeyError` is a `LookupError` and matches
    neither arm, so a document short of a required field was reported as an
    internal crash (Codex review).
    """

    @pytest.mark.parametrize(
        ("call", "document", "field"),
        [
            pytest.param(EntityId.from_dict, {}, "kind", id="entity-kind"),
            pytest.param(
                EntityId.from_dict,
                {"kind": "function"},
                "qualified_name",
                id="entity-name",
            ),
            pytest.param(OccurrenceId.from_dict, {}, "entity", id="occurrence-entity"),
            pytest.param(
                OccurrenceId.from_dict,
                {"entity": {"kind": "function", "qualified_name": "f"}},
                "observation",
                id="occurrence-observation",
            ),
            pytest.param(
                AvailabilityLedger.from_dict,
                {
                    "families": {},
                    "unknown_family_default": {"status": "not_collected"},
                    "overrides": [{}],
                },
                "family",
                id="ledger-override-family",
            ),
        ],
    )
    def test_a_missing_required_field_is_a_value_error(
        self, call: object, document: dict[str, object], field: str
    ) -> None:
        """Parametrized over every `from_dict` the sweep found, not the one
        that was reported — three of these five were never named.
        """
        with pytest.raises(ValueError, match=f"missing required field {field!r}"):
            call(document)

    def test_a_nested_document_names_itself_not_its_parent(self) -> None:
        """ "missing `kind`" alone does not say *which* entity was short of one.

        The nested case surfaces through the parent's `from_dict`, so the
        message has to carry the record it actually came from.
        """
        with pytest.raises(ValueError, match="an entity document is missing"):
            OccurrenceId.from_dict({"entity": {}, "observation": "ast"})

    @pytest.mark.parametrize(
        "versions",
        [
            pytest.param({"package_format_version": 1}, id="parsed-mapping"),
            pytest.param(None, id="none"),
            pytest.param("x", id="str"),
        ],
    )
    def test_the_decision_point_refuses_a_non_record(self, versions: object) -> None:
        """`check_reader_compatibility` leaked `AttributeError` from its first
        attribute access — the same misclassification, from the other side.
        """
        with pytest.raises(TypeError, match="versions must be a StorageVersions"):
            check_reader_compatibility(versions)

    def test_malformed_contents_of_a_real_record_still_degrade(self) -> None:
        """The control that keeps the fix from being too broad.

        Only the record itself must be one; a real record carrying nonsense
        in its informational axes still degrades rather than raising, which
        is a separate and deliberate rule.
        """
        versions = StorageVersions.from_dict(
            {
                "package_format_version": 1,
                "comparison_contract_version": 1,
                "normalization_recipe": {"a": 1},
                "section_schema_versions": ["not-a-mapping"],
            }
        )
        assert check_reader_compatibility(versions).readable is True

    def test_no_from_dict_reaches_a_required_field_by_raw_subscript(self) -> None:
        """The sweep as a test, since doing it by hand keeps missing doors.

        The report named one `from_dict`; there were four. This enumerates
        them instead of asserting a conclusion, so the fifth fails here
        rather than in review.
        """
        import ast

        offenders: list[str] = []
        for path in adr062_module_paths():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef) or node.name != "from_dict":
                    continue
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Subscript)
                        and isinstance(sub.value, ast.Name)
                        and sub.value.id in ("data", "raw", "doc", "payload")
                        and isinstance(sub.slice, ast.Constant)
                        and isinstance(sub.slice.value, str)
                    ):
                        offenders.append(f"{path.name}:{sub.lineno} {ast.unparse(sub)}")

        assert offenders == [], (
            "these `from_dict` sites read a required field by raw subscript, so a "
            f"truncated document raises KeyError instead of ValueError: {offenders}"
        )

    def test_every_from_dict_validates_its_container_first(self) -> None:
        """The container's *shape* is checked before any field is read.

        `required_field` is not that check: it reaches a key by subscript, so
        an object supplying `__getitem__` without `.get` clears it and then
        leaks `AttributeError` when an optional field is read through `.get` —
        the boundary this package documents as "the reader is broken", for
        input that is merely malformed. Both `entity_ids.py` doors had exactly
        that gap while every sibling already guarded, which is the shape a
        per-site habit produces and a rule does not.

        Enumerating the doors rather than asserting the conclusion: a
        `from_dict` added later fails here instead of in review.
        """
        import ast

        def guards_container_first(fn: ast.FunctionDef) -> bool:
            for stmt in fn.body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                    continue  # docstring
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    call = stmt.value
                    if isinstance(call.func, ast.Name) and call.func.id in (
                        "_mapping",
                        "mapping",
                    ):
                        return True
                if isinstance(stmt, ast.If):
                    # `if not isinstance(data, Mapping): ...` -- the degrading
                    # form `versioning.py` uses deliberately, which is still a
                    # container check made before any field is read.
                    return "isinstance" in ast.unparse(stmt.test)
                return False
            return False

        unguarded: list[str] = []
        for path in adr062_module_paths():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef) or node.name != "from_dict":
                    continue
                if not guards_container_first(node):
                    unguarded.append(f"{path.name}:{node.lineno}")

        assert unguarded == [], (
            "these `from_dict` doors read a field before checking their container "
            "is a mapping, so a dict-like object without `.get` leaks "
            f"AttributeError instead of TypeError: {unguarded}"
        )

    def test_no_from_dict_reads_a_row_field_without_guarding_the_row(self) -> None:
        """The level underneath the door, which the sweep above does not reach.

        Guarding a `from_dict`'s own parameter says nothing about the rows it
        then reads out of that parameter. `AvailabilityLedger.from_dict`
        guarded its container and its `families` shape and still read each
        *override row*'s identifying fields by subscript, so a
        `__getitem__`-only row was accepted and reserialized as valid storage
        — the same defect one level down, found by review rather than by the
        sweep that was supposed to have closed the class.

        So the rule is restated over every value that reaches
        `required_field`, not only the parameter: whatever name it is called
        on must have been passed to the mapping guard first, in the same
        function.
        """
        import ast

        def guarded_names(fn: ast.FunctionDef) -> set[str]:
            names = set()
            for sub in ast.walk(fn):
                if not isinstance(sub, ast.Call):
                    continue
                func = sub.func
                if isinstance(func, ast.Name) and func.id in ("_mapping", "mapping"):
                    if sub.args and isinstance(sub.args[0], ast.Name):
                        names.add(sub.args[0].id)
                # `isinstance(x, Mapping)` counts too -- versioning.py's
                # degrading form checks the same thing without raising.
                if isinstance(func, ast.Name) and func.id == "isinstance":
                    if sub.args and isinstance(sub.args[0], ast.Name):
                        names.add(sub.args[0].id)
            return names

        offenders: list[str] = []
        for path in adr062_module_paths():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef) or node.name != "from_dict":
                    continue
                safe = guarded_names(node)
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Name)
                        and sub.func.id in ("_required_field", "required_field")
                        and sub.args
                        and isinstance(sub.args[0], ast.Name)
                        and sub.args[0].id not in safe
                    ):
                        offenders.append(
                            f"{path.name}:{sub.lineno} required_field({sub.args[0].id})"
                        )

        assert offenders == [], (
            "these sites read a required field off a value never checked to be a "
            f"mapping, so a dict-like row is accepted as valid storage: {offenders}"
        )


class TestRowSequenceFieldsRejectEveryWrongContainer:
    """A field holding rows is a JSON array, and nothing else iterates safely.

    Python will iterate a mapping (yielding its **keys**), a string
    (characters), or a set (in an order that varies by process) into
    something plausible. Every one of those failures is silent, and when the
    container is empty all of them produce the *claim* that the producer
    established there are no rows — the "absence is not evidence" reading
    this package exists to prevent (Codex review).
    """

    _ENTITY = {"kind": "function", "qualified_name": "f"}

    @pytest.mark.parametrize(
        "container",
        [
            pytest.param({}, id="empty-mapping"),
            pytest.param({"a": 1}, id="mapping"),
            pytest.param("", id="empty-str"),
            pytest.param("ab", id="str"),
            pytest.param(set(), id="empty-set"),
            pytest.param(b"", id="bytes"),
        ],
    )
    def test_every_row_field_refuses_it(self, container: object) -> None:
        """All four sites, not the three that were reported.

        `IdentityConflict.from_dict`'s own occurrences field had the
        identical gap and was not named — found by sweeping for the shape
        rather than fixing the reports.
        """
        cases = [
            lambda: AvailabilityLedger.from_dict(
                {
                    "families": {},
                    "unknown_family_default": {"status": "not_collected"},
                    "overrides": container,
                }
            ),
            lambda: OccurrenceId.from_dict(
                {
                    "entity": self._ENTITY,
                    "observation": "ast",
                    "attributes": container,
                }
            ),
            lambda: OccurrenceSet.from_dict({"occurrences": container}),
            lambda: IdentityConflict.from_dict(
                {"reason": "r", "occurrences": container}
            ),
        ]
        for call in cases:
            with pytest.raises(TypeError, match="must be a sequence of rows"):
                call()

    def test_a_mapping_would_have_manufactured_identity(self) -> None:
        """The sharpest instance, stated as its consequence.

        `{("size", "8"): "discarded"}` was read as the attribute
        `("size", "8")` with the mapping's value dropped — an identity
        component invented from one half of a mapping, which then decides
        what `OccurrenceSet.add` treats as a duplicate.
        """
        with pytest.raises(TypeError, match="attributes must be a sequence"):
            OccurrenceId.from_dict(
                {
                    "entity": self._ENTITY,
                    "observation": "ast",
                    "attributes": {("size", "8"): "discarded"},
                }
            )

    def test_real_arrays_and_optional_fields_still_work(self) -> None:
        """The control, corrected: an *omitted* field is not always benign.

        This asserted that `OccurrenceSet.from_dict({})` reads as an empty
        set — the same absence-is-emptiness reading the ledger's own
        `families` test encoded, and wrong for the same reason. `to_dict`
        writes `occurrences` unconditionally, so its absence is truncation,
        and treating it as "the producer found no observations" is the claim
        invariant 3 forbids (Codex review).

        A genuinely optional field — one `to_dict` omits at its default —
        does still default, which is the other half of the rule and is swept
        over every record in
        `TestEveryUnconditionallyWrittenKeyIsRequired`.
        """
        with pytest.raises(ValueError, match="missing required field"):
            OccurrenceSet.from_dict({})
        assert OccurrenceSet.from_dict({"occurrences": []}).to_dict() == {
            "occurrences": []
        }
        assert (
            OccurrenceId.from_dict(
                {"entity": self._ENTITY, "observation": "ast"}
            ).attributes
            == ()
        )
        assert (
            AvailabilityLedger.from_dict(
                {
                    "families": {},
                    "unknown_family_default": {"status": "not_collected"},
                    "overrides": [],
                }
            ).overrides
            == {}
        )

    def test_no_document_field_is_iterated_without_a_container_check(self) -> None:
        """The sweep as a test, since three rounds of these were reported.

        Walks every `from_dict` and fails on a `data.get(...)` used directly
        as an iterable, so the next such field is caught here rather than in
        review.
        """
        import ast

        offenders: list[str] = []
        for path in adr062_module_paths():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef) or node.name != "from_dict":
                    continue
                for sub in ast.walk(node):
                    iterables = []
                    if isinstance(sub, ast.comprehension):
                        iterables.append(sub.iter)
                    elif isinstance(sub, ast.For):
                        iterables.append(sub.iter)
                    for iterable in iterables:
                        text = ast.unparse(iterable)
                        if ".get(" in text and "_row_sequence" not in text:
                            offenders.append(f"{path.name}:{sub.lineno} {text}")

        assert offenders == [], (
            "these document fields are iterated without checking the container, "
            f"so a mapping yields keys and an empty one reads as 'none': {offenders}"
        )


class TestConstructorsGuardContainersToo:
    """The parse door and the assignment door must agree about a container.

    `526cd29` added `row_sequence` to `from_dict` and stopped there, so
    direct construction still accepted a mapping — and `OccurrenceId`'s own
    `__post_init__` already carried a comment recording that exact
    boundary-only-guard gap for row *shape*, one level in (Codex review).
    The gap was reintroduced one level out, in the same commit that quoted
    the lesson.
    """

    _ENTITY = EntityId(kind=EntityKind.FUNCTION, qualified_name="f")

    @pytest.mark.parametrize(
        "container",
        [
            pytest.param({("size", "8"): "discarded"}, id="mapping"),
            pytest.param("ab", id="str"),
            pytest.param({("size", "8")}, id="set"),
        ],
    )
    def test_the_constructor_refuses_what_from_dict_refuses(
        self, container: object
    ) -> None:
        with pytest.raises(TypeError, match="attributes must be a sequence of rows"):
            OccurrenceId(
                entity=self._ENTITY,
                observation=ObservationKind.AST,
                attributes=container,
            )

    def test_the_parse_guard_is_not_redundant(self) -> None:
        """Verified rather than assumed, because I assumed wrong first.

        Adding the check to `__post_init__` looked like it made the
        `from_dict` one redundant — one door, per this package's own rule.
        It does not: that comprehension *materializes* a mapping's keys into
        a tuple, so the constructor receives a perfectly valid sequence.
        Removing it reopened the parse path, and only re-running the
        reproduction caught it.
        """
        with pytest.raises(TypeError, match="attributes must be a sequence of rows"):
            OccurrenceId.from_dict(
                {
                    "entity": {"kind": "function", "qualified_name": "f"},
                    "observation": "ast",
                    "attributes": {("size", "8"): "discarded"},
                }
            )

    def test_extend_refuses_a_mapping_but_keeps_every_other_iterable(self) -> None:
        """A narrower rule than `row_sequence`, deliberately.

        `extend` takes an `Iterable`, so a generator is a legitimate caller
        and `row_sequence` would reject one. A `set` is accepted for a
        checked reason rather than an assumed one: `add` keeps each bucket
        in key order, so the resulting state is canonical whatever order a
        set iterated in. Only a mapping changes what the call means — its
        keys pass every per-item guard while its values vanish.
        """
        first = OccurrenceId(entity=self._ENTITY, observation=ObservationKind.AST)
        second = OccurrenceId(entity=self._ENTITY, observation=ObservationKind.DWARF)

        with pytest.raises(TypeError, match="must not be a mapping"):
            OccurrenceSet().extend({first: "dropped"})

        for accepted in (
            [first, second],
            {first, second},
            (item for item in (first, second)),
        ):
            built = OccurrenceSet()
            built.extend(accepted)
            assert len(built) == 2

    def test_a_valid_construction_is_untouched(self) -> None:
        occurrence = OccurrenceId(
            entity=self._ENTITY,
            observation=ObservationKind.AST,
            attributes=(("size", "8"),),
        )
        assert occurrence.attributes == (("size", "8"),)


class TestImplementationStateIsNotConstructorSurface:
    """`OccurrenceSet`'s index is two mappings, and both were `__init__` args.

    That let a caller install state `add` would have refused. The milder
    half leaked `AttributeError` from `to_dict()` — the wrong error kind for
    a malformed record, per this package's own contract. The sharper half is
    that the two mappings are *one index in two parts*, so they could be
    desynchronized: `_by_entity` holding an occurrence whose entity is
    missing from `_entities` made `len()` report it while `entities()` could
    not expose it (Codex review).

    `__len__` is documented here as "the number nothing may reduce", so that
    is this module's own invariant failing through a door it never meant to
    open.
    """

    def test_the_index_cannot_be_supplied_at_construction(self) -> None:
        entity = EntityId(kind=EntityKind.FUNCTION, qualified_name="f")
        occurrence = OccurrenceId(entity=entity, observation=ObservationKind.AST)

        with pytest.raises(TypeError, match="unexpected keyword argument"):
            OccurrenceSet(_by_entity={entity.key: ["bad"]})

        with pytest.raises(TypeError, match="unexpected keyword argument"):
            OccurrenceSet(_by_entity={entity.key: [occurrence]}, _entities={})

    def test_the_supported_way_in_still_works(self) -> None:
        """`add` is already the only way in, and it is public.

        Validating supplied state instead of refusing it would have meant
        rebuilding it through `add` — the same thing as not accepting it.
        """
        entity = EntityId(kind=EntityKind.FUNCTION, qualified_name="f")
        occurrence = OccurrenceId(entity=entity, observation=ObservationKind.AST)

        built = OccurrenceSet()
        built.add(occurrence)

        assert len(built) == 1
        assert [e.qualified_name for e in built.entities()] == ["f"]
        assert OccurrenceSet.from_dict(built.to_dict()) == built

    def test_no_dataclass_exposes_private_state_as_a_parameter(self) -> None:
        """The sweep, since this door was found by review rather than by me.

        `OccurrenceSet` was the only one; this fails if a future dataclass
        adds a private field without `init=False`.
        """
        import ast

        exposed: list[str] = []
        for path in adr062_module_paths():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if not any("dataclass" in ast.unparse(d) for d in node.decorator_list):
                    continue
                for stmt in node.body:
                    if not isinstance(stmt, ast.AnnAssign):
                        continue
                    if not isinstance(stmt.target, ast.Name):
                        continue
                    if not stmt.target.id.startswith("_"):
                        continue
                    default = ast.unparse(stmt.value) if stmt.value else ""
                    if "init=False" not in default:
                        exposed.append(
                            f"{path.name}:{stmt.lineno} {node.name}.{stmt.target.id}"
                        )

        assert exposed == [], (
            "these dataclasses expose private state as constructor parameters, "
            f"so a caller can install what the public mutators refuse: {exposed}"
        )


class TestEveryContainerGuardRefusesABinaryBuffer:
    """The enumerated-list mistake, made twice in one branch.

    `canonical.py` was told earlier in this PR that checking `(bytes,
    bytearray)` is only as complete as the list, and switched to the buffer
    protocol. I then wrote three new guards here checking `(str, bytes)`,
    so `bytearray` and `memoryview` walked straight through (Codex review).

    Worse for two of them than the empty-scalar case: `bytearray` and
    `memoryview` are `Sequence`s that are not `bytes`, so a **non-empty**
    one passed `row_sequence` and `key_collection` outright and would have
    yielded ints as rows.
    """

    @pytest.mark.parametrize(
        "buffer",
        [
            pytest.param(bytearray(), id="empty-bytearray"),
            pytest.param(bytearray(b"ab"), id="bytearray"),
            pytest.param(memoryview(b""), id="empty-memoryview"),
            pytest.param(memoryview(b"ab"), id="memoryview"),
            pytest.param(b"", id="empty-bytes"),
            pytest.param(b"ab", id="bytes"),
        ],
    )
    def test_all_three_guards_refuse_it(self, buffer: object) -> None:
        from abicheck.storage.guards import (
            item_iterable,
            key_collection,
            row_sequence,
        )

        for guard in (row_sequence, key_collection, item_iterable):
            with pytest.raises(TypeError):
                guard(buffer, "field")

    def test_the_doors_that_use_them_refuse_it_too(self) -> None:
        """The guards are only worth what their call sites do with them."""
        from abicheck.storage.identity import OccurrenceSet, group_by_entity

        for buffer in (bytearray(), memoryview(b""), bytearray(b"ab")):
            with pytest.raises(TypeError):
                OccurrenceSet().extend(buffer)
            with pytest.raises(TypeError):
                group_by_entity(buffer)

    def test_the_predicate_has_one_definition(self) -> None:
        """`canonical` and `guards` had reached this rule separately.

        An earlier note argued the two leaves should restate rules rather
        than import them, with tests pinning agreement — and the enumerated
        version then drifted into `guards` anyway, which is what that note
        predicted. They share the definition now, so agreement is structural
        rather than promised.
        """
        from abicheck.storage import canonical, guards

        assert canonical._is_binary_buffer is guards.binary_buffer

    def test_real_containers_are_untouched(self) -> None:
        """The control: a buffer is refused, an ordinary container is not."""
        from abicheck.storage.guards import (
            item_iterable,
            key_collection,
            row_sequence,
        )

        assert row_sequence([1, 2], "field") == (1, 2)
        key_collection(["layout"], "field")
        item_iterable(iter([1, 2]), "field")


class TestStrictIntRejectsBoolAndFloat:
    """``strict_int`` (ADR-063 Phase 2): a plain ``instance_of(x, int, ...)``
    would accept ``bool`` (it subclasses ``int``) and would not catch a
    ``float`` that happens to compare equal to a real int -- both traps this
    guard exists to close for a field whose two distinct wire values must
    never collapse onto one meaning (an ordinal, a schema version).
    """

    @pytest.mark.parametrize("bogus", [True, False, 2.0, "2", None, [2]])
    def test_bool_float_and_other_non_int_types_are_refused(
        self, bogus: object
    ) -> None:
        from abicheck.storage.guards import strict_int

        with pytest.raises(TypeError, match="must be an int"):
            strict_int(bogus, "field")

    def test_a_real_int_passes_through(self) -> None:
        from abicheck.storage.guards import strict_int

        assert strict_int(2, "field") == 2
        assert strict_int(0, "field") == 0


class TestRecordOperandsAreCheckedBeforeUse:
    """`narrowed` was the one record-taking method without an operand check.

    `record.narrowed(None)` leaked `AttributeError` from `other.status` —
    neither arm of the `TypeError`/`ValueError` pair this package documents
    as "the package is malformed", so a caller separating a corrupt package
    from a broken reader read it as the second (Codex review).
    """

    @pytest.mark.parametrize(
        "operand",
        [
            pytest.param(None, id="none"),
            pytest.param("x", id="str"),
            pytest.param({"status": "present"}, id="parsed-mapping"),
            pytest.param(1, id="int"),
        ],
    )
    def test_narrowed_refuses_a_non_record(self, operand: object) -> None:
        record = FactAvailability(status=FactStatus.PRESENT)

        with pytest.raises(TypeError, match="other must be a FactAvailability"):
            record.narrowed(operand)

    def test_a_real_narrowing_is_untouched(self) -> None:
        """The control — the guard must not disturb the combining rule."""
        family = FactAvailability(status=FactStatus.PRESENT)
        override = FactAvailability(status=FactStatus.FAILED)

        assert family.narrowed(override).status is FactStatus.FAILED

    def test_every_record_taking_method_checks_its_operand(self) -> None:
        """The sweep, which is how `narrowed` turned out to be the only one.

        Every sibling already guarded — `declare`, `override`, `add`,
        `occurrences_of`, `is_ambiguous` — so this was a single hole rather
        than a class. It is a test anyway, because "the only one" is the
        kind of claim this branch has repeatedly had falsified.
        """
        import ast

        records = {
            "AvailabilityLedger",
            "EntityId",
            "FactAvailability",
            "IdentityConflict",
            "OccurrenceId",
            "OccurrenceSet",
            "ProducerIdentity",
            "StorageVersions",
        }
        unguarded: list[str] = []
        for path in adr062_module_paths():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for fn in node.body:
                    if not isinstance(fn, ast.FunctionDef):
                        continue
                    if fn.name.startswith("_"):
                        continue
                    body = ast.unparse(fn)
                    # Same widening as the identity sweep's, for the same
                    # reason: a keyword-only record operand would have
                    # escaped a sweep that reads `fn.args.args` alone
                    # (CodeRabbit review).
                    for arg in (
                        *fn.args.posonlyargs,
                        *fn.args.args,
                        *fn.args.kwonlyargs,
                    ):
                        if arg.arg == "self" or arg.annotation is None:
                            continue
                        if ast.unparse(arg.annotation).strip('"') not in records:
                            continue
                        if "_instance_of" in body or "_availability" in body:
                            continue
                        unguarded.append(
                            f"{path.name}:{fn.lineno} {node.name}.{fn.name}({arg.arg})"
                        )

        assert unguarded == [], (
            "these public methods dereference a record operand without checking "
            f"it, so a non-record leaks AttributeError: {unguarded}"
        )


class TestIdentityDocumentsRefuseADictLikeWithoutGet:
    """The reported shape: `__getitem__` present, `.get` absent.

    An adapter handing back a row proxy, a `dbm`-style store, or any mapping
    look-alike that never inherited `Mapping` satisfies every required field
    by subscript and then fails on the first optional one. The failure is
    real either way; what matters is which side of the boundary it lands on,
    because a caller catching `TypeError`/`ValueError` around a load treats
    an `AttributeError` as its own bug rather than as bad input.
    """

    class _SubscriptOnly:
        """Supplies `__getitem__` and nothing else -- not a `Mapping`."""

        def __init__(self, **fields: object) -> None:
            self._fields = fields

        def __getitem__(self, key: str) -> object:
            return self._fields[key]

    def test_entity_from_dict_refuses_it(self) -> None:
        import pytest

        from abicheck.storage import EntityId

        doc = self._SubscriptOnly(kind="function", qualified_name="ns::f")
        with pytest.raises(TypeError, match="entity document"):
            EntityId.from_dict(doc)  # type: ignore[arg-type]

    def test_occurrence_from_dict_refuses_it(self) -> None:
        import pytest

        from abicheck.storage import OccurrenceId

        doc = self._SubscriptOnly(
            entity={"kind": "function", "qualified_name": "ns::f"},
            observation="export_table",
        )
        with pytest.raises(TypeError, match="occurrence document"):
            OccurrenceId.from_dict(doc)  # type: ignore[arg-type]

    def test_a_real_mapping_still_round_trips(self) -> None:
        """The control: guarding the door must not close it.

        A guard on a parse is one edit away from rejecting the valid input it
        was meant to admit, and the round trip is the property that would
        actually be lost.
        """
        from abicheck.storage import OccurrenceId

        original = OccurrenceId.from_dict(
            {
                "entity": {"kind": "function", "qualified_name": "ns::f"},
                "observation": "export_table",
                "container": "libfoo.so",
                "attributes": [["version", "GLIBC_2.2"]],
            }
        )
        assert OccurrenceId.from_dict(original.to_dict()) == original


class TestOverrideRowsAreCheckedIndividually:
    """A guarded array does not make its rows guarded.

    The ledger already refused a non-mapping container and a `families` value
    that was not a mapping. An override *row* was still read by subscript, so
    a dict-like row supplying `family`, `entity` and `availability` was
    accepted and would be reserialized as valid storage — a malformed
    document laundered into a well-formed one, which is worse than a crash
    because nothing downstream can tell.
    """

    class _SubscriptOnly:
        def __init__(self, **fields: object) -> None:
            self._fields = fields

        def __getitem__(self, key: str) -> object:
            return self._fields[key]

    def test_a_dict_like_override_row_is_refused(self) -> None:
        import pytest

        from abicheck.storage import AvailabilityLedger

        row = self._SubscriptOnly(
            family="exports",
            entity="libfoo.so",
            availability={"status": "present"},
        )
        with pytest.raises(TypeError, match="override document"):
            AvailabilityLedger.from_dict(
                {
                    "families": {},
                    "unknown_family_default": {"status": "not_collected"},
                    "overrides": [row],
                }
            )

    def test_a_real_override_row_still_round_trips(self) -> None:
        """The control: the row that should be accepted still is."""
        from abicheck.storage import AvailabilityLedger

        document = {
            "families": {},
            "unknown_family_default": {"status": "not_collected"},
            "overrides": [
                {
                    "family": "exports",
                    "entity": "libfoo.so",
                    "availability": {"status": "present"},
                }
            ],
        }
        ledger = AvailabilityLedger.from_dict(document)
        assert AvailabilityLedger.from_dict(ledger.to_dict()).to_dict() == (
            ledger.to_dict()
        )


class TestAFabricatingMappingCannotInventARequiredField:
    """`KeyError` is not what "absent" means for every mapping.

    A `defaultdict` is a `Mapping`, so the container guard admits it, and
    then `__missing__` returns an invented value rather than raising — so
    the field looked present. For an identity document that is the worst
    available outcome: the fabricated value is not rejected, it is
    *reserialized as genuine identity*, and occurrences would go on to
    deduplicate under a key no adapter ever supplied (Codex review).

    Every door is covered rather than the reported one. The guard is shared,
    so one fix covers them all — but "the shared guard was fixed" and "every
    caller of it is actually safe" are different claims, and this branch has
    had the second one falsified after asserting the first.
    """

    @staticmethod
    def _fabricating(**present: object) -> Any:
        from collections import defaultdict

        return defaultdict(lambda: "fabricated", present)

    def test_entity_document(self) -> None:
        with pytest.raises(ValueError, match="qualified_name"):
            EntityId.from_dict(self._fabricating(kind="function"))

    def test_occurrence_document(self) -> None:
        with pytest.raises(ValueError, match="observation"):
            OccurrenceId.from_dict(
                self._fabricating(
                    entity={"kind": "function", "qualified_name": "ns::f"}
                )
            )

    def test_identity_conflict_document(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            IdentityConflict.from_dict(self._fabricating(occurrences=[]))

    def test_override_document(self) -> None:
        with pytest.raises(ValueError, match="entity"):
            AvailabilityLedger.from_dict(
                {
                    "families": {},
                    "unknown_family_default": {"status": "not_collected"},
                    "overrides": [self._fabricating(family="exports")],
                }
            )

    def test_a_plain_dict_still_reports_the_same_missing_field(self) -> None:
        """The control: the ordinary missing-field path is unchanged.

        Membership is now tested before the subscript, so the branch that
        used to raise is no longer the one that runs — the observable
        behaviour for a genuinely truncated document must not have moved
        with it.
        """
        with pytest.raises(ValueError, match="missing required field"):
            EntityId.from_dict({"kind": "function"})


class TestEveryUnconditionallyWrittenKeyIsRequired:
    """The rule behind four separate reports, stated once and measured.

    `from_dict` may default a field only where `to_dict` omits it
    conditionally. A key the writer always emits cannot be absent in a
    document that writer produced, so absence means truncation — and
    defaulting it converts damage into a positive claim: an empty
    collection asserts "the producer ran and established nothing is
    there" (`AGENTS.md` invariant 3).

    Four instances of this were reported one at a time — `overrides`,
    then `families` alongside it, then `occurrences` in two modules, with
    `unknown_family_default` and `status` found by applying the rule
    rather than waiting for the fifth report. Enumerating it removes the
    need for a sixth: a record whose writer gains an unconditional key
    fails here unless its reader requires it.

    A *minimal* instance is what makes this mechanical. Every optional
    field is omitted by `to_dict` at its default, so whatever a minimal
    instance still emits is exactly the unconditional set — no list of
    key names to maintain, and no way for the test to disagree with the
    writer.
    """

    @pytest.mark.parametrize(
        "record",
        [
            pytest.param(EntityId(EntityKind.FUNCTION, "ns::f"), id="entity"),
            pytest.param(
                OccurrenceId(
                    EntityId(EntityKind.FUNCTION, "ns::f"), ObservationKind.AST
                ),
                id="occurrence",
            ),
            pytest.param(OccurrenceSet(), id="occurrence-set"),
            pytest.param(
                IdentityConflict(
                    reason="two spellings",
                    occurrences=(
                        OccurrenceId(
                            EntityId(EntityKind.FUNCTION, "ns::f"),
                            ObservationKind.AST,
                        ),
                        OccurrenceId(
                            EntityId(EntityKind.FUNCTION, "ns::f"),
                            ObservationKind.DWARF,
                        ),
                    ),
                ),
                id="identity-conflict",
            ),
            pytest.param(FactAvailability(FactStatus.PRESENT), id="availability"),
            pytest.param(AvailabilityLedger(), id="ledger"),
        ],
    )
    def test_dropping_any_key_the_writer_always_emits_is_refused(
        self, record: Any
    ) -> None:
        document = record.to_dict()
        assert document, "a minimal record must still emit its required keys"

        for key in document:
            truncated = {k: v for k, v in document.items() if k != key}
            with pytest.raises((TypeError, ValueError)):
                type(record).from_dict(truncated)

    @pytest.mark.parametrize(
        ("full", "minimal"),
        [
            pytest.param(
                EntityId(EntityKind.FUNCTION, "ns::f", discriminator="d"),
                EntityId(EntityKind.FUNCTION, "ns::f"),
                id="entity",
            ),
            pytest.param(
                OccurrenceId(
                    EntityId(EntityKind.FUNCTION, "ns::f"),
                    ObservationKind.AST,
                    container="libfoo.so",
                    attributes=(("version", "GLIBC_2.2"),),
                ),
                OccurrenceId(
                    EntityId(EntityKind.FUNCTION, "ns::f"), ObservationKind.AST
                ),
                id="occurrence",
            ),
            pytest.param(
                FactAvailability(FactStatus.PARTIAL, producer="dwarf"),
                FactAvailability(FactStatus.PARTIAL),
                id="availability",
            ),
        ],
    )
    def test_the_optional_keys_are_still_optional(
        self, full: Any, minimal: Any
    ) -> None:
        """The other half of the rule.

        Without this, "require everything" would satisfy the test above and
        break the round trip for precisely the fields `to_dict` omits to keep
        a document small. The optional set is derived the same mechanical
        way — whatever the full document carries that the minimal one does
        not — so neither half depends on a maintained list of key names.
        """
        document = full.to_dict()
        optional = set(document) - set(minimal.to_dict())
        assert optional, "this record was chosen for having optional keys"

        for key in optional:
            without = {k: v for k, v in document.items() if k != key}
            type(full).from_dict(without)  # must not raise


class TestAContainerThatDisagreesWithItself:
    """`required_field`'s second arm, which its own comment calls unreachable.

    The comment says "unreachable for a well-behaved mapping, kept because
    `in` and `[]` are two different methods and a container is free to
    disagree with itself between them". That is a claim about reachability,
    and an untested claim about reachability is how the first arm got written
    wrong in the first place — so it is checked rather than asserted.

    A mapping whose `__contains__` says yes and whose `__getitem__` raises is
    the exact disagreement named. It still has to land on the documented
    boundary: `ValueError`, not the raw `KeyError`.
    """

    class _Liar(dict):  # type: ignore[type-arg]
        def __contains__(self, key: object) -> bool:
            return True

    def test_the_second_arm_still_reports_the_documented_error(self) -> None:
        from abicheck.storage.guards import required_field

        with pytest.raises(ValueError, match="missing required field"):
            required_field(self._Liar(), "kind", "an entity document")

    def test_a_from_dict_door_survives_the_same_container(self) -> None:
        """Reached through a real door, not only the guard in isolation."""
        with pytest.raises(ValueError, match="missing required field"):
            EntityId.from_dict(self._Liar())


class TestRecordOrderingRefusesAForeignOperand:
    """`__lt__` returns `NotImplemented` rather than guessing an order.

    Python turns that into a `TypeError` at the comparison site, which is the
    right outcome: these keys order records against records, and an order
    invented against an unrelated object would sort silently rather than
    fail. Covered because "it returns NotImplemented" is a contract, and the
    arm is otherwise never executed by any test in this package.
    """

    @pytest.mark.parametrize(
        "record",
        [
            pytest.param(EntityId(EntityKind.FUNCTION, "ns::f"), id="entity"),
            pytest.param(
                OccurrenceId(
                    EntityId(EntityKind.FUNCTION, "ns::f"), ObservationKind.AST
                ),
                id="occurrence",
            ),
        ],
    )
    def test_comparing_against_a_foreign_object_is_a_type_error(
        self, record: Any
    ) -> None:
        assert record.__lt__(object()) is NotImplemented
        with pytest.raises(TypeError):
            record < object()  # noqa: B015 - the comparison is the subject


class TestOverrideKeysMustBeAPair:
    """The assignment door's own shape check on `overrides` keys.

    `AvailabilityLedger.overrides` is keyed by `(family, entity)`. A key that
    is not a two-tuple would be validated field-by-field by the two
    `decision_key` calls below it, which index into it — so the shape has to
    be established before those run, and this pins that it is.
    """

    @pytest.mark.parametrize(
        "key",
        [
            pytest.param("layout", id="bare-string"),
            pytest.param(("layout",), id="one-tuple"),
            pytest.param(("layout", "ns::Foo", "extra"), id="three-tuple"),
            pytest.param(4, id="int"),
            # No list case: an unhashable key raises inside the dict literal,
            # before any door here sees it — that would test Python, not this
            # guard.
        ],
    )
    def test_a_non_pair_override_key_is_refused(self, key: Any) -> None:
        with pytest.raises(TypeError, match="\\(family, entity\\) pair"):
            AvailabilityLedger(overrides={key: FactAvailability(FactStatus.PRESENT)})
