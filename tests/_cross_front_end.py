"""ADR-049 Phase 1's cross-front-end gate, as test code.

"Every front end resolves equivalent semantic input to an equal
``CompatibilityEvaluationConfig`` and provenance receipt" is a property of
two resolutions, and no production path ever holds two -- each run resolves
one config. These checks were package functions only tests called; they
live here, beside the tests that run them
(``test_compatibility_evaluation_frontend.py``,
``test_compatibility_evaluation_receipts.py``).
"""

from __future__ import annotations

from typing import Any

from abicheck.compatibility_evaluation_config import (
    CompatibilityEvaluationConfig,
    ValueProvenance,
)
from abicheck.contract_relevance_types import SelectorLayer

_SECTIONS = (
    "contract",
    "evidence",
    "surface",
    "assurance",
    "policy",
    "gate",
    "versioning",
)


def _normalized_provenance(prov: ValueProvenance) -> tuple[Any, ...]:
    """*prov* with the two front-end-specific details dropped, and no more.

    ADR-049 D7 puts ``explicit_cli`` and ``api_request`` in one precedence
    tier, and the same semantic input is spelled differently by construction
    (``--policy`` vs. the ``policy`` field). Exactly those two -- which of the
    pair the layer is, and the option spelling recorded with it -- are
    legitimately different *records of how* a value was stated.

    **Everything else is compared**, including each entry's own ``identity``,
    ``sha256``, ``path``, and ``argument_index``: dropping the digest let two
    runs whose receipts name differently-digested policy files compare as
    equivalent, which is precisely the drift the digest exists to catch
    (Codex review).
    """
    explicit = {SelectorLayer.EXPLICIT_CLI, SelectorLayer.API_REQUEST}

    def _layer(value: SelectorLayer) -> str:
        return "explicit" if value in explicit else value.value

    return (
        _layer(prov.layer),
        prov.source_kind,
        prov.reference,
        prov.version,
        prov.sha256,
        prov.path,
        prov.field_location,
        tuple(
            (
                _layer(entry.layer),
                entry.argument_index,
                entry.path,
                entry.identity,
                entry.sha256,
            )
            for entry in prov.selected_by
        ),
        None
        if prov.shadowed_legacy is None
        else _normalized_provenance(prov.shadowed_legacy),
    )


def cross_front_end_differences(
    a: CompatibilityEvaluationConfig, b: CompatibilityEvaluationConfig
) -> list[str]:
    """Every way *a* and *b* differ beyond which front end stated them.

    The executable form of ADR-049 Phase 1's gate: "every front end resolves
    equivalent semantic input to an equal ``CompatibilityEvaluationConfig``
    and provenance receipt." Values must be equal outright; provenance must be
    equal after normalizing the one permitted difference
    (:func:`_normalized_provenance`). Returns a human-readable list so a
    failing comparison says *which* field diverged, not merely that one did.
    """
    differences: list[str] = []
    for section in _SECTIONS:
        if getattr(a, section) != getattr(b, section):
            differences.append(
                f"{section}: {getattr(a, section)!r} != {getattr(b, section)!r}"
            )
    if a.suppressions != b.suppressions:
        differences.append(f"suppressions: {a.suppressions!r} != {b.suppressions!r}")

    keys_a, keys_b = set(a.provenance), set(b.provenance)
    for missing in sorted(keys_a ^ keys_b):
        differences.append(f"provenance: {missing!r} present on only one side")
    for key in sorted(keys_a & keys_b):
        norm_a = _normalized_provenance(a.provenance[key])
        norm_b = _normalized_provenance(b.provenance[key])
        if norm_a != norm_b:
            differences.append(f"provenance[{key!r}]: {norm_a!r} != {norm_b!r}")
    return differences


#: The tiers whose hops name an input the *caller* stated, as opposed to a
#: key inside a file the caller pointed at. Only these are checked against a
#: request type's fields -- see :func:`unstatable_selectors`.
_REQUEST_STATED_LAYERS = frozenset(
    {SelectorLayer.API_REQUEST, SelectorLayer.LEGACY_ALIAS}
)


def unstatable_selectors(
    config: CompatibilityEvaluationConfig, *, request_type: type | None = None
) -> list[str]:
    """Every hop in *config* that names an input its own layer cannot state.

    A receipt exists so a run's inputs can be identified and replayed, so a
    hop claiming an input the caller never had is worse than a missing one:
    it is confidently wrong. Four instances have now been found by review:
    the original explicit-candidate default that motivated ``spell()``,
    ``--policy``/``--scope-public-headers`` on a ``ScanRequest``,
    ``--severity-preset`` on the MCP tool, and -- once those were routed through
    ``spell()`` -- a ``ScanRequest`` receipt naming ``CompareRequest``'s
    ``scope_public``/``policy_file_path``/``suppress``, which is a *different*
    entity's field list.

    Two checks, because that fourth instance proved the first insufficient
    on its own:

    * every ``API_REQUEST`` hop must not name a CLI flag (a candidate built
      with a hard-coded ``"--flag"`` instead of going through ``spell()``);
    * given *request_type*, every hop at a *front-end-stated* tier
      (``API_REQUEST`` and ``LEGACY_ALIAS``) must name a real field of it.
      Without this, "not a flag" passes for any plausible-looking
      identifier, which is exactly how one wrong spelling was replaced by
      another. Pass the dataclass the front end actually accepts.

    ``LEGACY_ALIAS`` is included deliberately, and only under *request_type*:
    the reported ``scope_public`` hop sat at that tier, not ``API_REQUEST``
    (``--policy``/``scope_public`` are D7 aliases for the fields they
    select), so a check restricted to the request tier would have passed
    the very defect it was written for. Layers that describe a *file*
    (``PROJECT_CONFIG``, ``RUN_RECIPE``) are excluded: those hops correctly
    name config keys such as ``severity.preset``, which are not request
    fields and never should be.

    :func:`cross_front_end_differences` structurally cannot catch either:
    :func:`_normalized_provenance` drops option spellings *on purpose*, since
    the same semantic input is legitimately spelled differently per front
    end. That normalization is what makes the equality gate meaningful and
    also what makes it blind here, so this is a separate check rather than a
    stricter setting of that one.

    Returns human-readable descriptions so a failure names the field and the
    spelling, not merely that one exists. Deliberately one-directional: a CLI
    hop carrying a bare field name is not an error, because several CLI
    inputs (a project-config key, a composed scope) genuinely have no flag.
    """
    import dataclasses

    known: frozenset[str] | None = None
    request_name = ""
    if request_type is not None and dataclasses.is_dataclass(request_type):
        known = frozenset(f.name for f in dataclasses.fields(request_type))
        request_name = request_type.__name__
    offenders: list[str] = []
    for field_name in sorted(config.provenance):
        prov = config.provenance[field_name]
        chain = [
            prov,
            *([] if prov.shadowed_legacy is None else [prov.shadowed_legacy]),
        ]
        for entry in chain:
            for hop in entry.selected_by:
                option = hop.option or ""
                if hop.layer is SelectorLayer.API_REQUEST and option.startswith("--"):
                    offenders.append(
                        f"{field_name}: api_request hop names the CLI flag "
                        f"{option!r}, which no API caller can pass"
                    )
                elif (
                    known is not None
                    and option
                    and hop.layer in _REQUEST_STATED_LAYERS
                    and option not in known
                ):
                    offenders.append(
                        f"{field_name}: {hop.layer.value} hop names {option!r}, "
                        f"which is not a field of {request_name}"
                    )
    return offenders
