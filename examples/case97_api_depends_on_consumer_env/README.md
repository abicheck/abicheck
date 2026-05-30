# case97 — public API depends on consumer build environment (RISK)

## What this case demonstrates

Header `v1.h` declares `lib::extended()` only when the consumer
defines `USE_FEATURE`. A consumer who *does* define it sees a strictly
larger public API than one who does not. Any program that links objects
from both consumer modes has an inconsistent public surface.

## Why a dedicated detector

A plain side-by-side diff cannot see this — it only ever sees the
surface produced by *one* configuration. The matrix-aware
`API_DEPENDS_ON_CONSUMER_ENV` detector fires when the probe harness is
invoked with a manifest that exposes both macro states; it diffs the
declared-name sets across configurations within a single library
version and emits one finding per divergent declaration.

## Expected verdict

Per single-config build this case is a normal `func_removed` between
v1 (built with `USE_FEATURE`) and v2 (built without). The matrix
finding is layered on top by the probe-harness pipeline and reported as
`COMPATIBLE_WITH_RISK`.
