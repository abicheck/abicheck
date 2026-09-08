# Workflow: approve a known, intentional break

**Task:** "I renamed a function on purpose — how do I stop CI from failing
on it, without turning off detection for everything else?"

This is a Phase 5 slice of the examples/catalog split (retired plan
record, `docs/contribute/plans/index.md`) — a small,
curated, task-oriented example independent of the 197-case calibration
catalog under `catalog/cases/` (which exists to calibrate detectors, not to
teach the CLI). See `../../CLAUDE.md` for the rest of this curated set
(Phase 5 is complete, 7 of 7).

## The project

A tiny shared library, `color`, renames its one public function between
releases:

```text
v1/color.h   RGB to_rgb(int hex);
v2/color.h   RGB convert_to_rgb(int hex);   -- to_rgb renamed, on purpose
```

To a diffing tool this looks like a function removed (`to_rgb`) plus an
unrelated function added (`convert_to_rgb`) — a real ABI break for any
binary still calling `to_rgb`, even though the maintainer already reviewed
and intends it.

## Run it without suppression first

```bash
cd examples/workflows/suppressions

# Build both releases as shared libraries
python3 build_shared_lib.py -fPIC -g v1/color.c -o libcolor_v1.so
python3 build_shared_lib.py -fPIC -g v2/color.c -o libcolor_v2.so

# Compare — this reports the rename as a break, correctly
abicheck compare libcolor_v1.so libcolor_v2.so \
    --header old=v1/color.h --header new=v2/color.h
```

```text
Verdict: BREAKING (exit 4)

- func_removed: Public function removed: to_rgb
- func_added: New public function: convert_to_rgb
```

## Approve it with a suppression rule

`suppressions.yaml` names the exact symbol and records *why* it's accepted:

```yaml
version: 1
suppressions:
  - symbol: to_rgb
    reason: "Renamed to convert_to_rgb in v2; approved API rename, tracked in RELEASE_NOTES"
```

```bash
abicheck compare libcolor_v1.so libcolor_v2.so --header old=v1/color.h --header new=v2/color.h --suppress suppressions.yaml
```

## What you get

```text
Verdict: COMPATIBLE (exit 0)

> 1 change(s) suppressed via suppression file
>   - to_rgb — Public function removed: to_rgb
```

Exit code is now `0` — but abicheck also prints an honest caveat you should
not skip past:

> 1 major-class finding(s) (func_removed) were suppressed (intent:
> unspecified) (rule(s): symbol='to_rgb'), so this comparison is **not** a
> proven-compatible release: the binary ABI break was hidden from the
> verdict, not shown to be absent.

A suppression rule changes what CI *gates on*, never what actually
happened: the underlying ABI break is still real, and the report's
`suppression` block (`--format json`) always records what was suppressed,
by which rule, and why — "100 removals detected, 100 suppressed by rule X"
never silently disappears. See [Suppressions](../../../docs/use/suppressions.md)
for the full rule syntax (regex/glob selectors, `namespace`, `expires`,
`reachability`, and when a broad rule needs `allow_public_break`).

## Next steps

- Add `expires: 2026-12-31` to a rule to make an approval temporary — an
  expired rule is ignored, so the break starts gating again automatically
  instead of staying silenced forever.
- Suppressions are step 2 of a four-step CI gating pipeline (classify →
  suppress → severity → exit code) — see [CI Gating](../../../docs/use/ci-gating.md)
  for how they combine with `--policy` reclassification and
  `--severity-preset`.
- Want machine-readable output for CI? Add `--format json` — every
  suppressed finding is listed under `suppression.suppressed_changes`, so a
  dashboard can still show what was silenced even though it didn't gate.
