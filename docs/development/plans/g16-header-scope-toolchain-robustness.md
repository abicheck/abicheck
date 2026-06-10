# G16 — Header-scoped source-mode toolchain robustness & actionable diagnostics

**Registry:** `UC-TC-header-scope-robustness` (`planned`)
**Effort:** M · **Risk:** medium (host-toolchain matrix is large; castxml/clang quirks)

## Problem

Header-scoped scans (`--headers`, the castxml source path in
`abicheck/dumper.py` / `abicheck/dumper_castxml.py`) are the only way abicheck
can separate *public source API* from *private/internal* surface — the single most-requested
disambiguation in the real-world scan campaign. But in the 2026-06 real-world
cron the header-scoped re-run **aborted before any ABI comparison** in 21
separate issue records, always for the same small family of host-toolchain
parse failures, never for an abicheck logic bug:

- **glibc sized-float types** — `unknown type name '_Float32'` (also
  `_Float64`/`_Float128`) when castxml's clang frontend parses the host
  `/usr/include/stdlib.h` / `<math.h>` pulled in transitively
  (`ISSUE-RW-20260606-1539-04`, `-1509-03`, `-1645-03`, libuv/krb5/benchmark/fmt).
- **GCC 13 libstdc++ attribute** — `__assume__` attribute parse failure through
  C++ system headers (`ISSUE-RW-20260606-1509-03`).
- **`--lang c` + `extern "C"`** — C headers guarded by
  `#ifdef __cplusplus extern "C"` fail in explicit `--lang c` mode even though
  the guard is correct, because castxml always drives clang in a C++-ish mode
  (`ISSUE-RW-20260606-1539-04`, `-1645-03`).

The net effect is that the *binary-only* verdict (correctly `BREAKING` on a
removed export) cannot be contrasted against a *public-header-scoped* verdict,
so the campaign cannot tell "real public-API break" from "internal/private
export churn" for C/C++ libraries on a stock GCC/glibc host. abicheck already
has a precise hint for one symptom (the `--lang c` class/namespace mismatch in
`_validate_castxml_output`), which proves the pattern works — the remaining
symptoms just have no equivalent guidance and surface as a raw
`castxml failed (exit N)` stderr dump.

This is **not** the G4 problem. G4 adds a *libclang AST extractor* to observe
source constructs castxml cannot emit (concepts, `explicit`, ctor mangling).
G16 is about making the *existing* castxml header path **survive a stock host
toolchain** and, when it cannot, **fail with an actionable, single-line hint**
instead of an opaque compiler error.

## Supported toolchain floor

The durable cure for the `_FloatN` and `__assume__` aborts is **a castxml built
against a new enough Clang**, because the failure is purely a frontend-version
mismatch (castxml drives an internal Clang while emulating the host GCC). The
recommended floor is **bundled Clang ≥ 18** (`_Float32/64/128` land in Clang 16;
the `[[assume]]` / `__assume__` attribute in Clang 18). `abicheck/dumper.py`
encodes this as `_RECOMMENDED_CLANG_MAJOR` and, on a failure, probes
`castxml --version` to tell the user which version they have and what to upgrade
to. `--lang c` + `extern "C"` is a *usage* issue (don't force C), handled by the
diagnostic, not a version floor.

### Why not "just fix it in CastXML" / shim it away?

A `-D_Float32=float …` preprocessor shim was prototyped and **rejected**: glibc's
`bits/floatn-common.h` emits its own `typedef float _Float32;` fallback in some
configurations, which the shim rewrites into the invalid `typedef float float;`
— so it can break the very headers it is meant to rescue (flagged in PR review).
abicheck cannot reliably out-parse a frontend that is simply older than the host
headers, so it **diagnoses precisely and recommends the upgrade** rather than
guessing. The structural cure is the libclang extractor (**G4**), which parses
with the host's own libclang and removes the dependence on castxml's bundled-Clang
cadence entirely.

## Goal & acceptance criteria

- [x] A parse-failure classifier recognises the known host-header symptoms
      (`_Float32`/`_Float64`/`_Float128`, GCC `__assume__`, the `--lang c` +
      `extern "C"` case) in castxml stderr and surfaces a specific,
      copy-pasteable remediation. *Done:* `_castxml_failure_hint` in
      `abicheck/dumper.py`; the hint is appended to the raised `SnapshotError`.
- [x] On a sized-float/`__assume__` failure, probe `castxml --version` and fold
      the detected version + the recommended Clang floor into the remediation.
      *Done:* `_parse_castxml_version` / `_castxml_version_note` +
      `_RECOMMENDED_CLANG_MAJOR`.
- [ ] `abicheck compare --headers` over a tiny header that transitively includes
      `<math.h>` succeeds (or degrades with the actionable hint) on the CI host,
      asserted end-to-end. *Remaining* (needs the `integration` toolchain).
- [x] The diagnostic text, the version parser, and the version note are
      unit-tested without a live compiler. *Done:*
      `tests/test_castxml_toolchain_robustness.py`.
- [ ] Promote to a dedicated `HeaderToolchainError` so callers can branch on the
      class. *Remaining.*
- [~] A reliable host-side *workaround* (vs. diagnosis). The `-D_FloatN` shim was
      rejected (see above); the durable path is the Clang-floor recommendation
      plus the libclang extractor (**G4**).

## Design

1. **Classifier** — `_castxml_failure_hint(stderr, …)` maps the known stderr
   signatures → a remediation string, mirroring the existing `--lang c` hint;
   appended to the `SnapshotError` raised by `_validate_castxml_output`.
2. **Version probe** — on failure, `_castxml_version_note()` runs
   `castxml --version`, parses it with the pure `_parse_castxml_version`, and adds
   "Detected castxml X (clang Y); needs clang ≥ 18 — upgrade" when below the floor.
3. **CLI surfacing** — the `compare`/`dump --headers` path renders the remediation
   as a single actionable line (not a 2 000-char stderr blob); a dedicated
   `HeaderToolchainError` so callers can branch is still open.

## Files & surfaces

- `abicheck/dumper.py` — `_castxml_failure_hint`, `_parse_castxml_version`,
  `_castxml_version_note`, `_RECOMMENDED_CLANG_MAJOR`, wired into
  `_validate_castxml_output`.
- `abicheck/errors.py` — `HeaderToolchainError` (still open).
- `abicheck/cli.py` / `abicheck/service.py` — render the remediation line.

## Tests

- `tests/test_castxml_toolchain_robustness.py` (done) — the captured stderr
  signatures (`_Float32`, `__assume__`, `--lang c` + `extern "C"`) → assert the
  specific remediation text; the version parser and the version note (message
  only, no compiler).
- `tests/test_header_scope_toolchain.py` (new, `integration`) — a header that
  `#include <math.h>` parses (or yields the hint) end-to-end on a GCC/glibc host.

## Example fixtures

- A minimal `examples/`-style C header that transitively pulls in sized-float
  types, used by the integration test to prove the host-header path survives.

## Effort & risk

M. The classifier + version-probe + message tests are small and high-value
(they turn 21 dead-end campaign runs into one-line, user-fixable diagnostics)
and have shipped. The remaining risk sits in any *automatic* host-side
workaround: the `-D_FloatN` preprocessor shim was prototyped and **rejected**
(it rewrites glibc's own `typedef float _Float32;` fallback into the invalid
`typedef float float;`), so abicheck deliberately stops at precise diagnosis +
the Clang-floor recommendation. The structural cure is the libclang extractor
(**G4**), not a brittle shim.

## Out of scope

- Observing source constructs castxml cannot emit (concepts/`explicit`/ctor
  mangling) — that is **G4**.
- Shipping a bundled compiler/sysroot. G16 makes the *host* toolchain usable or
  clearly diagnoses it; it does not vendor a toolchain.
