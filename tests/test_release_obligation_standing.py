"""The release surface narrows an `-I`-reached obligation exactly as a member does.

Known gap "An `-I`-reached sibling library's headers yield LOW-confidence
export obligations; release reconciliation and JSON confidence not yet
narrowed": the member ``public_not_exported`` check reports a declaration
outside the run's declared ``-H`` surface at LOW confidence, worded as not
established; the release reconciliation (``policy.
release_contract_reconciliation``) still reported the same declaration at
HIGH, and the release JSON's ``missing_exports`` carried no confidence.

Bug class ``evidence.narrowed_conclusion_not_carried_across_cardinality``:
one declaration, one recorded scope -> one confidence, whichever path (scalar
member check or release reconciliation) and whichever wire form (in-memory
``Change``, release JSON, stored surface round-trip) reads it. The oracle is
the truth table stated here from the ruling (narrowed iff the run recorded a
target root AND the declaration is ``unresolved`` with rule ``no_root``), not
``obligation_standing`` itself; both paths are checked against it, over the
full small domain and for both entity kinds.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.buildsource.cross_source_checks import (
    CrosscheckConfig,
    _check_public_not_exported,
)
from abicheck.compare.bundle_export_index import build_bundle_export_index
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.declarations import Function, Variable
from abicheck.model.evidence_status import Confidence
from abicheck.model.extraction_scope import EntityOwnership, ExtractionScope
from abicheck.model.fact import Fact
from abicheck.model.ownership_rules import OwnershipRules
from abicheck.model.release_surface import ReleasePublicSurface
from abicheck.model.vocabulary import AccessLevel, ScopeOrigin
from abicheck.policy.release_contract_reconciliation import reconcile_release
from abicheck.report.release_public_surface_section import (
    compute_release_public_surface,
)
from abicheck.workflows.release_surface_acquisition import surface_from_snapshot

_ROOTS = [(), ("include/pvxs/iochooks.h",)]
_CONTRACTS = [None, "public", "unresolved"]
_RULES = ["no_root", "no_file", "target_root:include/pvxs/iochooks.h"]
_ENTITIES = ["function", "variable"]


def _expected(roots: tuple[str, ...], contract: str | None, rule: str) -> Confidence:
    """The ruling, stated independently of the implementation."""
    narrowed = bool(roots) and contract == "unresolved" and rule == "no_root"
    return Confidence.LOW if narrowed else Confidence.HIGH


def _ownership(contract: str | None, rule: str) -> Fact | None:
    if contract is None:
        return None
    return Fact.present(EntityOwnership("pvxs", contract, rule))


def _snapshot(
    roots: tuple[str, ...], contract: str | None, rule: str, entity: str
) -> AbiSnapshot:
    fact = _ownership(contract, rule)
    extra = {} if fact is None else {"ownership_fact": fact}
    functions: list[Function] = []
    variables: list[Variable] = []
    if entity == "function":
        functions.append(
            Function(
                name="version_int",
                mangled="version_int",
                return_type="int",
                params=[],
                access=AccessLevel.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
                is_extern_c=True,
                source_location="include/pvxs/version.h:10",
                **extra,
            )
        )
    else:
        variables.append(
            Variable(
                name="version_int",
                mangled="version_int",
                type="int",
                access=AccessLevel.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
                source_location="include/pvxs/version.h:10",
                **extra,
            )
        )
    return AbiSnapshot(
        library="libpvxsIoc.so",
        version="1",
        functions=functions,
        variables=variables,
        from_headers=True,
        elf=ElfMetadata(
            soname="libpvxsIoc.so",
            needed=[],
            symbols=[ElfSymbol(name="unrelated", is_default=True)],
            imports=[],
        ),
        extraction_scope=(
            ExtractionScope(ownership_rules=OwnershipRules(target_roots=roots))
            if roots
            else None
        ),
    )


class _Member:
    def __init__(self) -> None:
        self.elf = ElfMetadata(
            soname="",
            needed=[],
            symbols=[ElfSymbol(name="unrelated", is_default=True)],
            imports=[],
        )


def _release_findings(surface: ReleasePublicSurface):  # type: ignore[no-untyped-def]
    index = build_bundle_export_index("new", {"libpvxsIoc.so": _Member()})
    return reconcile_release(surface, index)


_DOMAIN = list(itertools.product(_ROOTS, _CONTRACTS, _RULES, _ENTITIES))


@pytest.mark.parametrize(("roots", "contract", "rule", "entity"), _DOMAIN)
def test_member_and_release_agree_with_the_ruling(
    roots: tuple[str, ...], contract: str | None, rule: str, entity: str
) -> None:
    snapshot = _snapshot(roots, contract, rule, entity)
    expected = _expected(roots, contract, rule)

    member = _check_public_not_exported(snapshot, CrosscheckConfig())
    assert [(c.symbol, c.confidence) for c in member.findings] == [
        ("version_int", expected)
    ]

    surface = surface_from_snapshot(snapshot, acquisition_key="k", side="new")
    reconciliation = _release_findings(surface)
    assert [(c.kind, c.symbol, c.confidence) for c in reconciliation.findings] == [
        (ChangeKind.PUBLIC_NOT_EXPORTED, "version_int", expected)
    ]

    # The release JSON carries it as a field, not only in prose.
    terms = compute_release_public_surface(reconciliation)
    assert [f["confidence"] for f in terms.missing_exports] == [expected.value]

    # A stored surface (bundle-facts baseline) keeps the standing.
    restored = ReleasePublicSurface.from_dict(surface.to_dict())
    assert restored == surface
    assert [c.confidence for c in _release_findings(restored).findings] == [expected]


def test_narrowed_release_finding_is_worded_as_not_established() -> None:
    snapshot = _snapshot(
        ("include/pvxs/iochooks.h",), "unresolved", "no_root", "function"
    )
    surface = surface_from_snapshot(snapshot, acquisition_key="k", side="new")
    (finding,) = _release_findings(surface).findings
    assert "not established" in finding.description
    side = _release_findings(surface).new.to_dict()
    assert side["missing_from_bundle"] == [
        {
            "symbol": "version_int",
            "name": "version_int",
            "entity": "function",
            "source_location": "include/pvxs/version.h:10",
            "outside_declared_surface": True,
        }
    ]


def test_member_json_exposes_the_low_confidence() -> None:
    """The scalar report already carries per-finding confidence in
    ``impact_assessment.confidence`` (schema 2.15); pin that the narrowed
    finding reaches it, so the member half of the JSON gap stays closed."""
    from abicheck.report.change_json import (
        change_to_dict,
    )

    snapshot = _snapshot(
        ("include/pvxs/iochooks.h",), "unresolved", "no_root", "function"
    )
    (finding,) = _check_public_not_exported(snapshot, CrosscheckConfig()).findings
    payload = change_to_dict(finding)
    assert payload["impact_assessment"]["confidence"] == "low"  # type: ignore[index]
