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

import pytest

from abicheck.storage.availability import AvailabilityLedger
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
                {"overrides": [{}]},
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
        import pathlib

        offenders: list[str] = []
        for path in sorted(pathlib.Path("abicheck/storage").glob("*.py")):
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
            lambda: AvailabilityLedger.from_dict({"overrides": container}),
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

    def test_real_arrays_and_absent_fields_still_work(self) -> None:
        """The control. An *omitted* field is not a malformed one.

        The default is a real empty tuple, so absence still means "no rows
        stated" — only a wrongly-typed container is refused.
        """
        assert OccurrenceSet.from_dict({}).to_dict() == {"occurrences": []}
        assert OccurrenceSet.from_dict({"occurrences": []}).to_dict() == {
            "occurrences": []
        }
        assert (
            OccurrenceId.from_dict(
                {"entity": self._ENTITY, "observation": "ast"}
            ).attributes
            == ()
        )
        assert AvailabilityLedger.from_dict({"overrides": []}).overrides == {}

    def test_no_document_field_is_iterated_without_a_container_check(self) -> None:
        """The sweep as a test, since three rounds of these were reported.

        Walks every `from_dict` and fails on a `data.get(...)` used directly
        as an iterable, so the next such field is caught here rather than in
        review.
        """
        import ast
        import pathlib

        offenders: list[str] = []
        for path in sorted(pathlib.Path("abicheck/storage").glob("*.py")):
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
