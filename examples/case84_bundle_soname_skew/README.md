# Case 84: Multi-library bundle SONAME skew

**Category:** Cross-artifact ABI | **Verdict:** BREAKING

## What breaks

oneDAL ships as a co-versioned bundle:

```
libonedal_core.so.X
libonedal_thread.so.X
libonedal_sequential.so.X
libonedal_parameters.so.X
libonedal_dpc.so.X
libonedal_sycl.so.X
```

They are intended to move SONAME in lockstep. The break case84 encodes:
release engineering bumps five of the six (`.so.1 → .so.2`) but
`libonedal_thread.so.1` keeps the old SONAME by accident. Each library on
its own passes per-library ABI checks. The bundle does not:

- Distro packages declare `Depends: libonedal-core2, libonedal-thread1` —
  internally inconsistent.
- A binary linked against v1 finds `libonedal_thread.so.1` (still installed)
  and `libonedal_core.so.2` (the only one available) — the two were built
  against different internal contracts, leading to corruption at the first
  cross-library call.

## Layout

Unlike the other cases, this one has no single `v1.cpp/v2.cpp` pair. Instead
v1 and v2 are *directories* containing multiple `.so` files:

```
case84_bundle_soname_skew/
├── v1/
│   ├── libonedal_core.so.1
│   ├── libonedal_thread.so.1
│   └── libonedal_dpc.so.1
└── v2/
    ├── libonedal_core.so.2
    ├── libonedal_thread.so.1   <-- soname-laggard
    └── libonedal_dpc.so.2
```

The `compare-release` mode of abicheck ingests both directories and runs
the cross-library aggregator.

## Real Failure Demo

**Severity: BREAKING / RELEASE BUNDLE SKEW**

This fixture is a directory-level failure, not a single app swap. Build the
`v1/` and `v2/` directories and run `compare-release`: two siblings bump to
SONAME `.so.2`, while `libonedal_thread` stays on `.so.1`.

```bash
bash examples/case84_bundle_soname_skew/gen_bundle.sh
abicheck compare-release \
    examples/case84_bundle_soname_skew/v1 \
    examples/case84_bundle_soname_skew/v2 \
    --bundle-cohort libonedal_ \
    --format json
# -> "bundle_verdict": "BREAKING", bundle_findings include "bundle_soname_skew"
# -> exit code 4
```

The `--bundle-cohort` flag is required: SONAME-skew detection is **opt-in**.
You declare which libraries are co-versioned (by name prefix); without it
abicheck never infers a lockstep invariant from filenames, so an ordinary
release that bumps one independent library while another stays put is not
flagged.

The underlying cohort detector can also be driven directly:

```bash
python3 - <<'PY'
from abicheck.diff_onedal import bundle_members_from_directory, detect_bundle_soname_skew
old = bundle_members_from_directory('examples/case84_bundle_soname_skew/v1')
new = bundle_members_from_directory('examples/case84_bundle_soname_skew/v2')
for finding in detect_bundle_soname_skew(old, new, cohort_prefix='libonedal_'):
    print(finding.kind.value)
PY
# bundle_soname_skew
```

## Why this is its own ChangeKind

Existing `soname_changed` and `soname_bump_recommended` are per-library
signals. The skew is a property of the **set** of libraries; no individual
artifact is wrong. A new `BUNDLE_SONAME_SKEW` ChangeKind in
`BREAKING_KINDS` reports the cohort-level invariant: "five siblings bumped,
one did not."

## How abicheck detects it

`compare-release` builds a bundle snapshot of each release directory and
runs the bundle layer (`abicheck/bundle.py`). When one or more cohorts are
declared via `--bundle-cohort PREFIX`, `_detect_soname_skew` delegates to
`abicheck.diff_onedal.detect_bundle_soname_skew` for each declared cohort:
it extracts each member's SONAME major from both releases and emits one
`BUNDLE_SONAME_SKEW` finding when the cohort has mixed soname deltas (some
bumped, some not). The finding is classified `BREAKING`, so the bundle (and
therefore the overall) verdict is BREAKING. With no `--bundle-cohort` the
check is disabled — cohorts are never inferred from filenames.

Source files for the example: see `gen_bundle.sh` for the script that
produces the `.so` files. The end-to-end path is covered by
`tests/test_bundle.py::TestCompareReleaseBundleE2E`.
