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

"""Declarative ``bundle_variants:`` block parsing + a real ``pair_variants()``
driver (G38 Phase 13).

G38 Phase 3 shipped :func:`abicheck.bundle_multibuild.pair_variants` fully
implemented and tested, but with **no caller anywhere in this codebase**
outside its own test suite (confirmed by grep immediately before this module
was written: ``pair_variants(`` matches only ``bundle_multibuild.py`` itself
and ``tests/test_bundle_multibuild.py``). This module is that caller's
config-parsing half: a ``bundle_variants:`` mapping (variant name -> logical
identity coordinates + a ``required`` flag) is validated eagerly into
:class:`BundleVariantSpec` objects, then :func:`run_bundle_variant_pairing`
feeds real, caller-supplied :class:`~abicheck.bundle_facts.BundleFacts` per
variant into ``pair_variants`` and applies the ``required:`` distinction this
plan's own Phase 13 section asks for -- escalating a missing *required*
variant's ``BUNDLE_VARIANT_COVERAGE_REGRESSED`` finding to ``BREAKING``
(reusing the existing ADR-027 D3.2 ``BundleFinding.effective_verdict``
override mechanism, not a second, parallel gating path) instead of leaving
every missing variant at that kind's default ``RISK`` severity.

**Not wired into ``.abicheck.yml`` discovery.** The natural home for a new
top-level config block is ``BuildConfig`` in
``abicheck/buildsource/inline.py`` -- but that module is *at* the
AI-readiness 2000-line hard cap (confirmed by ``wc -l`` immediately before
this module was written), so a new block cannot be added there without
first splitting it, the identical file-size wall
``abicheck/bundle_side_input.py``'s own module docstring documents for the
stored-facts CLI consumer. This module therefore takes an already-parsed
raw ``dict`` (whatever a caller's own YAML/JSON loader produced for the
``bundle_variants:`` key) rather than reading ``.abicheck.yml`` itself --
the schema/validation half of "a declarative ``bundle_variants:`` config
block" this plan phase asks for, with the discovery half left as the
documented, scoped gap the plan doc's own Phase 13 status note names.

**Declared-vs-captured fingerprint verification (G38 Phase 13 follow-up).**
:func:`run_bundle_variant_pairing` can now optionally verify a captured
``BundleFacts.variant_fingerprint`` against what the declared
:class:`BundleVariantSpec` for that same name would itself compute
(``verify_fingerprints=True``) -- closing the gap this module's own
docstring used to leave open. It stays opt-in, default ``False``: every
*existing* caller (including this module's own test suite, which
deliberately pairs specs against hand-picked sentinel fingerprints that
have nothing to do with any real coordinates) is unaffected, and a facts
file carrying the ``DEFAULT_VARIANT_FINGERPRINT`` sentinel -- what every
capture ``--bundle-facts-out`` produces today, since no real capture
pipeline can be told a variant name yet (see above) -- is never treated as
a mismatch either way, since there is nothing for it to be verified
against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bundle_facts import BundleFacts
    from .bundle_models import BundleFinding

#: Recognized keys inside one variant's own mapping -- an unrecognized key
#: is a hard validation error (see `parse_bundle_variants_config`), matching
#: this codebase's existing `BuildConfig` strict-schema convention
#: (`buildsource/build_config_schema.py`) rather than silently ignoring a
#: typo'd key.
_VARIANT_KEYS = frozenset(
    {"target_triple", "compiler_family", "feature_toggles", "required"}
)


class BundleVariantsConfigError(ValueError):
    """A ``bundle_variants:`` block failed eager validation."""


@dataclass(frozen=True)
class BundleVariantSpec:
    """One named multibuild variant's declared identity coordinates.

    Mirrors :func:`abicheck.bundle_multibuild.variant_fingerprint`'s own
    explicit-coordinate parameter shape exactly (target triple, compiler
    family, feature toggles) -- deliberately no ``compiler_version``, for
    the identical "legitimately-drifting build state, not variant identity"
    reason that function's own docstring gives.
    """

    name: str
    target_triple: str = ""
    compiler_family: str = ""
    feature_toggles: dict[str, str] = field(default_factory=dict)
    #: When True, a variant present in the old release with no matching
    #: fingerprint in the new release (``VariantOutcome.OLD_ONLY``) gates
    #: the release rather than only demoting to a RISK-level
    #: ``BUNDLE_VARIANT_COVERAGE_REGRESSED`` finding -- see
    #: `run_bundle_variant_pairing`.
    required: bool = False

    def fingerprint(self) -> str:
        """This spec's own coordinates, fingerprinted the same way a real
        capture would have been (`bundle_multibuild.variant_fingerprint`)."""
        from .bundle_multibuild import variant_fingerprint

        return variant_fingerprint(
            target_triple=self.target_triple,
            compiler_family=self.compiler_family,
            feature_toggles=self.feature_toggles,
        )


def parse_bundle_variants_config(
    raw: dict[str, object],
) -> dict[str, BundleVariantSpec]:
    """Validate a raw ``bundle_variants:`` mapping into ``{name: BundleVariantSpec}``.

    *raw* is the already-YAML/JSON-parsed value of a ``bundle_variants:`` top-
    level key (a caller's own document, not read from ``.abicheck.yml`` by
    this function -- see the module docstring for why). Validates eagerly and
    completely before returning anything: a malformed entry is a hard
    :class:`BundleVariantsConfigError`, never a silent no-op or a partial
    result a caller might not notice, matching this codebase's own
    ``PolicyFile``/``BuildConfig`` "hard load error, not warning-and-skip"
    convention for a structurally invalid config document.
    """
    if not isinstance(raw, dict):
        raise BundleVariantsConfigError(
            f"bundle_variants: must be a mapping of variant name -> spec, "
            f"got {type(raw).__name__}"
        )
    specs: dict[str, BundleVariantSpec] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not name:
            raise BundleVariantsConfigError(
                f"bundle_variants: variant names must be non-empty strings, "
                f"got {name!r}"
            )
        if not isinstance(entry, dict):
            raise BundleVariantsConfigError(
                f"bundle_variants.{name}: must be a mapping, got "
                f"{type(entry).__name__}"
            )
        unknown = set(entry) - _VARIANT_KEYS
        if unknown:
            raise BundleVariantsConfigError(
                f"bundle_variants.{name}: unrecognized key(s) "
                f"{sorted(unknown)!r} -- known keys are "
                f"{sorted(_VARIANT_KEYS)!r}"
            )
        target_triple = entry.get("target_triple", "")
        compiler_family = entry.get("compiler_family", "")
        feature_toggles = entry.get("feature_toggles", {})
        required = entry.get("required", False)
        if not isinstance(target_triple, str):
            raise BundleVariantsConfigError(
                f"bundle_variants.{name}.target_triple: must be a string, "
                f"got {type(target_triple).__name__}"
            )
        if not isinstance(compiler_family, str):
            raise BundleVariantsConfigError(
                f"bundle_variants.{name}.compiler_family: must be a string, "
                f"got {type(compiler_family).__name__}"
            )
        if not isinstance(feature_toggles, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in feature_toggles.items()
        ):
            raise BundleVariantsConfigError(
                f"bundle_variants.{name}.feature_toggles: must be a mapping "
                f"of string -> string"
            )
        if not isinstance(required, bool):
            raise BundleVariantsConfigError(
                f"bundle_variants.{name}.required: must be a boolean, got "
                f"{type(required).__name__}"
            )
        specs[name] = BundleVariantSpec(
            name=name,
            target_triple=target_triple,
            compiler_family=compiler_family,
            feature_toggles=dict(feature_toggles),
            required=required,
        )
    return specs


@dataclass(frozen=True)
class BundleVariantPairingResult:
    """Outcome of :func:`run_bundle_variant_pairing`."""

    #: Every OLD_ONLY comparison's finding (see
    #: `bundle_multibuild.coverage_regression_findings`), with a *required*
    #: variant's own finding carrying `effective_verdict=Verdict.BREAKING`
    #: (ADR-027 D3.2) so it gates the release instead of only demoting to
    #: this kind's default RISK severity.
    findings: list[BundleFinding]
    #: Names of every declared `required: true` variant with no matching
    #: fingerprint on the new side -- the same set `findings` above already
    #: encodes as BREAKING-escalated findings, surfaced separately so a
    #: caller can render/log it without re-deriving it from `findings`.
    missing_required_variants: list[str]


def run_bundle_variant_pairing(
    specs: dict[str, BundleVariantSpec],
    old_facts_by_variant: dict[str, BundleFacts],
    new_facts_by_variant: dict[str, BundleFacts],
    *,
    verify_fingerprints: bool = False,
) -> BundleVariantPairingResult:
    """Pair real, captured per-variant `BundleFacts` by fingerprint and apply
    each variant's declared `required:` gating.

    *old_facts_by_variant*/*new_facts_by_variant* are keyed by the same
    variant *names* `specs` declares -- a name present in one of these maps
    but absent from `specs` is accepted (an undeclared variant still pairs
    correctly by its own captured `BundleFacts.variant_fingerprint`, exactly
    as `bundle_multibuild.pair_variants` already allows); a declared
    `required: true` variant with no captured facts on the OLD side at all
    is not itself an error here, since `pair_variants` can only reason about
    fingerprints it was actually given -- the `required:` gate only ever
    escalates a real `OLD_ONLY` outcome `pair_variants` produced.

    *verify_fingerprints* (default `False`): when `True`, additionally
    checks -- for every name present in both `specs` and one of the two
    facts maps -- that the captured `BundleFacts.variant_fingerprint`
    equals what `specs[name].fingerprint()` itself computes for the same
    declared coordinates, raising `BundleVariantsConfigError` on a real
    mismatch (a `name` key paired with the wrong file is exactly the class
    of silent misconfiguration this check exists to catch: pairing would
    otherwise proceed using whichever fingerprint the facts file actually
    carries, silently ignoring that it disagrees with what was declared for
    that name). A facts file carrying `DEFAULT_VARIANT_FINGERPRINT` -- what
    every `--bundle-facts-out` capture produces today, since no real capture
    pipeline can be told a variant name yet (see this module's own file
    docstring) -- is never flagged as a mismatch, since it was never
    captured against any declared coordinates to verify against. Default is
    `False` because every *existing* caller (this module's own test suite
    included) pairs specs against arbitrary sentinel fingerprints that have
    nothing to do with any real coordinates -- turning this on
    unconditionally would make every such caller a hard error.
    """
    from .bundle_facts import DEFAULT_VARIANT_FINGERPRINT
    from .bundle_multibuild import coverage_regression_findings, pair_variants

    if verify_fingerprints:
        for facts_by_variant in (old_facts_by_variant, new_facts_by_variant):
            for name, facts in facts_by_variant.items():
                spec = specs.get(name)
                if spec is None:
                    continue
                actual = facts.variant_fingerprint
                if actual == DEFAULT_VARIANT_FINGERPRINT:
                    continue
                expected = spec.fingerprint()
                if actual != expected:
                    raise BundleVariantsConfigError(
                        f"bundle_variants.{name}: declared identity "
                        f"coordinates fingerprint to {expected!r}, but the "
                        f"captured BundleFacts file's own variant_"
                        f"fingerprint is {actual!r} -- this looks like the "
                        f"wrong file was assigned to variant {name!r}"
                    )

    comparisons = pair_variants(old_facts_by_variant, new_facts_by_variant)
    findings = coverage_regression_findings(comparisons)

    missing_required: list[str] = []
    for finding in findings:
        spec = specs.get(finding.symbol)
        if spec is not None and spec.required:
            from .checker_policy import Verdict

            finding.effective_verdict = Verdict.BREAKING
            finding.modulation_reason = (
                f"bundle_variants: '{finding.symbol}' is declared required: "
                f"true and has no matching build variant in the new release"
            )
            finding.modulation_rule = "bundle_variants.required"
            missing_required.append(finding.symbol)
    return BundleVariantPairingResult(
        findings=findings, missing_required_variants=missing_required
    )


def load_bundle_facts_by_variant(paths: dict[str, Path]) -> dict[str, BundleFacts]:
    """Convenience loader: ``{variant_name: facts_path}`` ->
    ``{variant_name: BundleFacts}``, for a caller wiring
    `run_bundle_variant_pairing` from real files on disk."""
    from .serialization import load_bundle_facts

    return {name: load_bundle_facts(path) for name, path in paths.items()}
