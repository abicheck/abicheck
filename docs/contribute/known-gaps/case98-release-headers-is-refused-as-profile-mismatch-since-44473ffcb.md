# case98 release-headers is refused as a profile mismatch since 44473ffcb

**Status (2026-10-25): OPEN.** Behavior change, not yet ruled on.

Since commit `44473ffcb` (#1778, "infer C++ dialect and target flags from
binary for header parse", `EXTRACTOR_GENERATION` 12 -> 13), a `release-headers`
compare of `case98_cxx_standard_floor_raised` no longer returns a verdict. The
header parse now takes the dialect from the binary, so the v1 and v2 snapshots
record different `language_standard` values, and the compare refuses with
`reason.kind = "profile_mismatch"` (ADR-050 D2): `verdict: null`,
`operational: not_comparable`.

Evidence: the same run at the parent commit gives `NO_CHANGE` (a documented
false negative, `known_gap` scoped to `debug-headers`, `release-headers`,
`stripped-headers`). At `44473ffcb` it gives no verdict. `debug-headers` still
reports a verdict for this case.

What the guard does now: `tests/check_stripped_fp.py` treats a declared-gap
XFAIL with no verdict as `unverified` (listed, not failed). That keeps the
release-headers job green without hiding the change.

Open question for the maintainer: is the refusal the intended outcome for a
standard change between two un-debugged builds (ADR-050 says refuse rather than
guess), or should the release-headers snapshots record the same dialect? The
`known_gap` text still describes a `NO_CHANGE` verdict and is stale for this
variant until that is decided.
