# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Equivalence between `pack_application.apply_to_compare_config` (single-
pair `compare`'s own gate-pack fold) and `policy.release_gate_options
.apply_release_gate_pack` (the directory/package release fan-out's own
mirror of that identical logic).

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
`ResolvedCompareConfig`-shaped object of its own to fold packs onto (a full
unification of the two *severity-level* applications is still ADR-064's own
named, deferred "PR G2" prerequisite work -- see below). Two
independently-reasoned implementations of one algorithm is exactly this
repo's own AGENTS.md "Primitive-level property tests" case -- so rather than
a risky, out-of-scope rewrite, this test was written to pin the two
implementations to agree on outcome, so a change to one that silently drifts
from the other fails here first.

**The exit-code-scheme half of that duplication has since been closed**
(ADR-063 Track A, 7B, follow-up PR): `policy.release_gate_options
.resolve_gate_pack_exit_code_scheme` is now the one function both
`apply_release_gate_pack` and `apply_to_compare_config` call for "which way
does the scheme move" -- the specific piece with the real regression history
(Codex review, PR #1032) this module's own docstring above describes. What
remains genuinely separate is the *severity-level* application itself
(`dataclasses.replace` on an already-resolved `SeverityConfig` for
`apply_to_compare_config`, six independent raw-string overrides for
`apply_release_gate_pack`) -- not duplicated logic so much as the same
update expressed against two different pre-resolution data shapes, which is
exactly the "no `ResolvedCompareConfig`-shaped object to fold onto" gap PR G2
is scoped to close for real. This test is kept, unchanged in what it
asserts, as the black-box parity guard over the whole fold (both the now-
shared piece and the still-separate one) rather than being narrowed now that
part of what it guards is implemented once instead of twice.

**Scope, precisely.** Both sides are driven from the identical "pre-pack"
severity/scheme state -- a real `ResolvedCompareConfig` built by
`resolve_compare_config` from a severity *preset* only (never per-category
CLI overrides: `resolve_compare_config`'s own docstring records that the
four `--severity-<category>` CLI flags were removed, and
`cli_compare_release.py`'s own `severity_abi_breaking`/etc. parameters are
themselves *not* a second CLI surface -- they are internal, `.abicheck.yml`-
resolved values `compare`'s directory/package fan-out forwards from that
very `ResolvedCompareConfig`, so a preset-only "pre-pack" state is the real
production shape, not a simplification). *pre_pack_scheme* is generated as
an explicit `"legacy"` or `"severity"` CLI choice (never `"auto"` -- neither
implementation's own "auto" resolution enters the comparison at all, since
the two differ in shape: concrete-and-final on `ResolvedCompareConfig`,
deferred to `resolve_release_gate_options`'s own later `PRESET_DEFAULT`
fallback on the release side) and threaded through *both* the CLI input
*and* `PackApplication.resolved_exit_code_scheme` -- in real production
these are the same resolved config's own `gate.exit_code_scheme` value
(`pack_application()`'s own construction), so decoupling them, as an
earlier revision of this test did, could never exercise the exact
regression `apply_to_compare_config`'s own docstring documents as
previously real (Codex review, PR #1032: a severity-only gate pack silently
overriding an explicit `--exit-code-scheme legacy`) -- an explicit `legacy`
pre-pack state with `resolved_exit_code_scheme` correctly still reading
`"legacy"` is what proves a severity-only pack cannot flip it (Codex
review, PR #1044, fifth round).

`exit_code_scheme` agreement is asserted unconditionally. Severity-content
agreement is asserted only when the *pack itself* did not force the final
scheme to `"legacy"` -- `resolve_release_gate_options` deliberately nulls
`GateOptions.severity` under a forced `"legacy"` scheme (gating is
controlled by scheme there), while `ResolvedCompareConfig.severity` stays
populated even in legacy mode (gating is controlled separately, by
`exit_code_scheme`, never by nulling the config) -- a deliberate, documented
asymmetry (`GateOptions`'s own docstring), not a bug this test should flag.
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
_PRESETS = ("default", "strict", "info-only")


@st.composite
def _pack_applications(
    draw: st.DrawFn, *, resolved_exit_code_scheme: str
) -> PackApplication:
    """A `PackApplication` carrying only a gate pack's own contribution --
    `policy_overrides`/`internal_namespaces` are irrelevant to either fold
    function under test, so they stay at their inert defaults.

    `severity_levels` values are real `SeverityLevel` enum members, matching
    `pack_application()`'s own real-production construction
    (`getattr(config.gate.severity, category)`, itself a `SeverityConfig`
    attribute) -- `apply_to_compare_config`'s `dataclasses.replace` call
    requires this (`SeverityConfig.__post_init__` rejects a plain string,
    Codex review per that class's own docstring), and `SeverityLevel` being
    a `str` subclass means the same values still satisfy
    `resolve_severity_config`'s `str | None` category parameters on the
    release side unchanged.

    *resolved_exit_code_scheme* is the caller's own already-resolved
    pre-pack scheme (see module docstring: coupled to the same value real
    production couples it to, never hard-coded)."""
    levels: dict[str, SeverityLevel] = {}
    for category in _SEVERITY_CATEGORIES:
        if draw(st.booleans()):
            levels[category] = draw(st.sampled_from(list(SeverityLevel)))
    scheme = draw(st.sampled_from([None, "legacy", "severity"]))
    return PackApplication(
        policy_overrides={},
        severity_levels=levels,
        exit_code_scheme=scheme,
        resolved_exit_code_scheme=resolved_exit_code_scheme,
    )


@given(
    preset=st.sampled_from(_PRESETS),
    pre_pack_scheme=st.sampled_from(["legacy", "severity"]),
    data=st.data(),
)
def test_severity_and_scheme_fold_agree_between_compare_and_release(
    preset: str, pre_pack_scheme: str, data: st.DataObject
) -> None:
    pack_application = data.draw(
        _pack_applications(resolved_exit_code_scheme=pre_pack_scheme)
    )
    resolved_cfg = resolve_compare_config(
        None,
        cli_severity_preset=preset,
        cli_scope_public=None,
        cli_exit_code_scheme=pre_pack_scheme,
    )
    assert (
        resolved_cfg.exit_code_scheme == pre_pack_scheme
    )  # the test's own precondition

    single_pair = apply_to_compare_config(resolved_cfg, pack_application)

    release = resolve_release_gate_options(
        pack_application,
        release_exit_code_scheme=pre_pack_scheme,
        severity_preset=preset,
        severity_abi_breaking=None,
        severity_potential_breaking=None,
        severity_quality_issues=None,
        severity_addition=None,
    )

    # Unconditional: both sides fold an identical pack's exit_code_scheme/
    # resolved_exit_code_scheme contribution the same way, from an
    # identical pre-pack starting scheme -- including an explicit `legacy`
    # pre-pack state, which a severity-only pack must never flip (the
    # regression this test's own docstring names).
    assert single_pair.exit_code_scheme == release.exit_code_scheme
    if pre_pack_scheme == "legacy" and pack_application.exit_code_scheme is None:
        assert single_pair.exit_code_scheme == "legacy"

    if release.exit_code_scheme == "legacy":
        # The one deliberate, documented asymmetry -- see module docstring.
        assert release.severity is None
        assert single_pair.severity is not None
        return

    assert release.severity is not None
    assert single_pair.severity == release.severity


@given(
    preset=st.sampled_from(_PRESETS),
    pre_pack_scheme=st.sampled_from(["legacy", "severity"]),
)
def test_a_pack_with_no_gate_contribution_is_a_no_op_on_both_sides(
    preset: str, pre_pack_scheme: str
) -> None:
    """`PackApplication`'s own contract ("every attribute is None/empty
    unless a pack actually supplied the value") means an inert pack must
    change nothing on either fold -- the degenerate case of the property
    above, pinned directly rather than only reachable by chance through the
    generator. Includes the explicit-`legacy` pre-pack state: an inert pack
    must leave it exactly as `"legacy"`, never flip it."""
    inert = PackApplication(policy_overrides={})
    assert inert.is_empty()

    resolved_cfg = resolve_compare_config(
        None,
        cli_severity_preset=preset,
        cli_scope_public=None,
        cli_exit_code_scheme=pre_pack_scheme,
    )
    single_pair = apply_to_compare_config(resolved_cfg, inert)
    assert single_pair == resolved_cfg

    release = resolve_release_gate_options(
        inert,
        release_exit_code_scheme=pre_pack_scheme,
        severity_preset=preset,
        severity_abi_breaking=None,
        severity_potential_breaking=None,
        severity_quality_issues=None,
        severity_addition=None,
    )
    assert release.exit_code_scheme == resolved_cfg.exit_code_scheme == pre_pack_scheme
    if pre_pack_scheme == "legacy":
        assert release.severity is None
    else:
        assert release.severity == resolved_cfg.severity
