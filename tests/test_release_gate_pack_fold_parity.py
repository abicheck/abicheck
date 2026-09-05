# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Equivalence between `pack_application.apply_to_compare_config` (single-
pair `compare`'s own gate-pack fold) and `policy.release_gate_options
.apply_release_gate_pack` (the directory/package release fan-out's own).

**Update (2026-09-05): the two no longer contain two copies of the fold.**
Duplication-and-convergence-assessment track T6 moved the fold rule itself
into one shared leaf, `policy/gate_pack_fold.fold_gate_pack_severity`, which
both callers now call -- so what this file guards has narrowed from "two
independently-reasoned implementations of one algorithm" to "two different
*shapes* around one shared implementation": `compare` folds onto an
already-resolved `SeverityConfig`, the release fan-out onto four raw
optional strings that may all still be `None`. That difference is real and
deliberate (a release run must keep "no severity setting in effect"
distinguishable from "the default levels", which a resolved config cannot
express), so it is still exactly where an outcome disagreement could appear,
and this property is still what would catch one. The shared primitive's own
contract is stated separately, as invariants, in
`tests/test_gate_pack_fold.py`. Collapsing the two *shapes* remains the
duplication-and-convergence-assessment plan's P0
`EffectiveGate`/`EffectiveEvaluationConfig` target.

**Why this test exists (ADR-063 Track 4, 7B investigation, 2026-09-03).**
7B's stated goal is a shared pair-operation executor closing the release
fan-out's "independent pair-semantics reimplementation" -- a close read of
every duplication axis that goal names found most of them already unified
through `service.run_compare`'s Tier-2 classification chokepoint
(suppression, pack policy/namespace overrides, depth enforcement, compile
context, and contract evaluation are all threaded through *that* one
function for every real per-library pair -- see `cli_compare_release_pairwise
._run_compare_pair`'s own docstring), or structurally justified rather than
a maintenance bug (the release fan-out's per-library JSON write has no
`--use-cases`/suppression-audit folding to share because `compare-release`
does not expose either flag at all; the build-config matrix's own
independent suppression/policy load is a synthetic pseudo-pair with no real
per-library dispatch to route through).

One real, self-documented duplication remained at investigation time:
`release_gate_options.py`'s own module docstring stated plainly that
`apply_release_gate_pack` "mirrors [`apply_to_compare_config`'s] *logic*...
instead" of sharing it, because the release fan-out has no
`ResolvedCompareConfig`-shaped object of its own to fold packs onto -- this
is distinct from ADR-064's own `GateOptions` rewrite (already landed
2026-09-02, and what `resolve_release_gate_options`/`apply_release_gate_pack`
themselves are part of), not that rewrite's still-open "PR G2" work; a full
fold unification is the duplication-and-convergence-assessment plan's own
P0 `EffectiveGate`/`EffectiveEvaluationConfig` target instead (not attempted
here -- see ADR-063 Track 4's 7B ledger entry,
`docs/_meta/one-semantic-pipeline-status.yaml`, for the full account, itself
corrected on review after this test first landed; T6 has since closed the
fold half of it, per the update at the top of this docstring). Two
independently-reasoned implementations of one algorithm is exactly this
repo's own AGENTS.md "Primitive-level property tests" case -- so rather than
a risky, out-of-scope rewrite, this test pinned the two implementations to
agree on outcome, so a change to one that silently drifted from the other
failed here first. It kept doing that job until the shared fold landed.

**Update (2026-09-04): CLI cleanup phase two PR G2 deleted the manual
`--exit-code-scheme` selector everywhere** (ADR-064's "Decision to encode"),
which makes most of what this file used to guard structurally impossible
now rather than merely untested: neither `apply_to_compare_config` nor
`resolve_release_gate_options`/`apply_release_gate_pack` accepts a scheme
override any more (`PackApplication.exit_code_scheme`/
`resolved_exit_code_scheme` and `resolve_release_gate_options`'s
`release_exit_code_scheme` parameter are gone), and `resolve_compare_config`
no longer accepts `cli_exit_code_scheme` at all -- there is nothing left to
pin, and so nothing left for a pack to be forbidden from overriding (the
`gate.exit_code_scheme` route into a `kind: gate` pack manifest is itself
gone, `PackManifestError`-rejected at load time, covered by
`test_pack_application.py`). This file no longer has a "pre-pack scheme"
input to compare, so it is narrowed to what still applies: given the *same*
severity preset and the *same* pack-supplied per-category severity levels,
both fold functions must derive the identical (now purely-derived)
`exit_code_scheme` and agree on the resulting severity content -- which
each still reaches through its own pre-resolution shape, even now that the
fold rule between those shapes is shared (the P0
`EffectiveGate`/`EffectiveEvaluationConfig` target's own remaining job to
unify for real, per the paragraph above).
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from abicheck.cli_helpers_compare import resolve_compare_config
from abicheck.pack_application import PackApplication, apply_to_compare_config
from abicheck.policy.release_gate_options import resolve_release_gate_options
from abicheck.policy.severity import SeverityLevel

_SEVERITY_CATEGORIES = (
    "abi_breaking",
    "potential_breaking",
    "quality_issues",
    "addition",
)
# None is the "no --severity-preset given" case, which resolves to the
# `legacy` scheme when no other severity source is in effect either.
_PRESETS = (None, "default", "strict", "info-only")


@st.composite
def _pack_applications(draw: st.DrawFn) -> PackApplication:
    """A `PackApplication` carrying only a gate pack's own severity-level
    contribution -- `policy_overrides`/`internal_namespaces` are irrelevant
    to either fold function under test, so they stay at their inert
    defaults, and there is no `exit_code_scheme`/`resolved_exit_code_scheme`
    field left on this dataclass at all (PR G2 deleted both).

    `severity_levels` values are real `SeverityLevel` enum members, matching
    `pack_application()`'s own real-production construction
    (`getattr(config.gate.severity, category)`, itself a `SeverityConfig`
    attribute) -- `apply_to_compare_config`'s `dataclasses.replace` call
    requires this (`SeverityConfig.__post_init__` rejects a plain string,
    Codex review per that class's own docstring), and `SeverityLevel` being
    a `str` subclass means the same values still satisfy
    `resolve_severity_config`'s `str | None` category parameters on the
    release side unchanged.
    """
    levels: dict[str, SeverityLevel] = {}
    for category in _SEVERITY_CATEGORIES:
        if draw(st.booleans()):
            levels[category] = draw(st.sampled_from(list(SeverityLevel)))
    return PackApplication(policy_overrides={}, severity_levels=levels)


@given(
    preset=st.sampled_from(_PRESETS),
    data=st.data(),
)
def test_severity_and_scheme_fold_agree_between_compare_and_release(
    preset: str | None, data: st.DataObject
) -> None:
    pack_application = data.draw(_pack_applications())

    resolved_cfg = resolve_compare_config(
        None,
        cli_severity_preset=preset,
        cli_scope_public=None,
    )
    single_pair = apply_to_compare_config(resolved_cfg, pack_application)

    release = resolve_release_gate_options(
        pack_application,
        severity_preset=preset,
        severity_abi_breaking=None,
        severity_potential_breaking=None,
        severity_quality_issues=None,
        severity_addition=None,
    )

    # Both sides derive the same scheme from the same inputs: a stated
    # preset OR a pack-supplied severity level activates `severity`;
    # neither present at all leaves `legacy`.
    assert single_pair.exit_code_scheme == release.exit_code_scheme
    if preset is None and not pack_application.severity_levels:
        assert single_pair.exit_code_scheme == "legacy"
    else:
        assert single_pair.exit_code_scheme == "severity"

    if release.exit_code_scheme == "legacy":
        # The one deliberate, documented asymmetry -- see module docstring.
        assert release.severity is None
        assert single_pair.severity is not None
        return

    assert release.severity is not None
    assert single_pair.severity == release.severity


@given(preset=st.sampled_from(_PRESETS))
def test_a_pack_with_no_gate_contribution_is_a_no_op_on_both_sides(
    preset: str | None,
) -> None:
    """`PackApplication`'s own contract ("every attribute is None/empty
    unless a pack actually supplied the value") means an inert pack must
    change nothing on either fold -- the degenerate case of the property
    above, pinned directly rather than only reachable by chance through the
    generator."""
    inert = PackApplication(policy_overrides={})
    assert inert.is_empty()

    resolved_cfg = resolve_compare_config(
        None,
        cli_severity_preset=preset,
        cli_scope_public=None,
    )
    single_pair = apply_to_compare_config(resolved_cfg, inert)
    assert single_pair == resolved_cfg

    release = resolve_release_gate_options(
        inert,
        severity_preset=preset,
        severity_abi_breaking=None,
        severity_potential_breaking=None,
        severity_quality_issues=None,
        severity_addition=None,
    )
    assert release.exit_code_scheme == resolved_cfg.exit_code_scheme
    if release.exit_code_scheme == "legacy":
        assert release.severity is None
    else:
        assert release.severity == resolved_cfg.severity
