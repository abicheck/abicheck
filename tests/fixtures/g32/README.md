# G32 Phase 0 fixtures

Regression fixtures for [ADR-050](../../../docs/contribute/adr/050-comparability-contract-and-multi-tu-manifest.md)
/ [G32](../../../docs/contribute/plans/g32-comparability-contract-and-multi-tu-manifest.md)
Phase 0. These are raw inputs (headers, and one real AST capture) for later
phases to load — not generated `.abi.json` snapshots, and (per Phase 0's own
"Out of scope") no production code reads any of this yet. `tests/test_g32_fixtures.py`
only asserts the fixtures themselves are present, non-empty, and structurally
sane.

## Fixture 1 — plain-clang AST capture vs. DPC++ multi-document capture

`plain_clang/header.h` is a small header; `plain_clang/ast_dump.json` is a
**real** `clang -x c++ -std=c++17 -fsyntax-only -Xclang -ast-dump=json
header.h` capture (Ubuntu clang 18.1.3), captured from inside this directory
so the embedded `"file"` path stays a stable, repo-relative `header.h`
rather than a throwaway absolute path. It parses as one JSON document (a
single `TranslationUnitDecl`).

**Real DPC++ multi-document capture (Phase 0's gap, closed):**
`dpcpp/header.h` is the same tiny `Point`/`add` shape as `plain_clang/header.h`
above (deliberately no `<sycl/sycl.hpp>` include — pulling in the real SYCL
runtime header balloons a single capture to multi-gigabyte AST dumps, and
`-fsycl` splits into a host + device compilation pass regardless of whether
the translation unit contains an actual offloaded kernel, so the tiny header
alone is enough to produce a genuine multi-document stream). `dpcpp/ast_dump.json`
is a **real**, unedited `icpx -fsycl -x c++ -std=c++17 -fsyntax-only -Xclang
-ast-dump=json -v header.h` capture (Intel oneAPI DPC++/C++ Compiler
2026.1.0, installed from `apt.repos.intel.com`'s `intel-oneapi-compiler-dpcpp-cpp`
package — see the compiler's own `--version` banner reproduced in
`dpcpp/compiler_invocation.log`), captured from inside this directory for
the same stable-relative-path reason as Fixture 1. Unlike Fixture 1, this
is genuinely **two** concatenated `TranslationUnitDecl` JSON documents on
stdout with no separator between them (`...}{...`) — a real
document-boundary-detection problem, not a guessed format.

`dpcpp/compiler_invocation.log` is the same invocation's **stderr** (`-v`
diagnostic output), captured separately from stdout. This is not incidental
noise: the raw AST JSON documents carry no `kind`/`target` field of their
own (`-ast-dump=json`'s output is ordinary clang AST-dump JSON, unaware of
the driver-level host/device split), so **`kind` and `target` for each
document must be correlated from the driver's own `-cc1 ... -triple <T>
... -fsycl-is-(host|device)` invocation lines on stderr, in the same order
the corresponding document appears on stdout** — confirmed against this
capture: the first stdout document corresponds to the first stderr `-cc1`
line (`-triple spir64-unknown-unknown ... -fsycl-is-device`, dominated by
injected OpenCL/SPIR builtin types like `__ocl_image2d_ro_t`), and the
second document to the second `-cc1` line (`-triple x86_64-unknown-linux-gnu
... -fsycl-is-host`, ordinary host builtins). Both documents still declare
the real `Point`/`add` from `header.h` at the tail of their `inner` list.
Phase D's `sycl_context.py` decoder is designed against this exact,
real correlation — not a guess at what DPC++ output "should" look like.

## Fixture 2 — ODR-safe merge pair and ODR-conflict pair

- `odr_safe/tu_a.h` forward-declares `struct Point`; `odr_safe/tu_b.h` gives
  it a full definition. A correct multi-TU merge (Phase C) combines these
  into one complete `Point`, not a conflict.
- `odr_conflict/tu_a.h` declares `int compute(int)`; `odr_conflict/tu_b.h`
  declares `double compute(int)` — the same name, genuinely incompatible
  signatures across two TUs, which a correct merge must reject.

## Fixture 3 — external STL noise

`stl_noise/public.h` declares `int sum_all(std::vector<int> values)` — a
genuinely public, reportable declaration whose signature also pulls in a
`std::vector<int>` instantiation that is supporting, not itself reportable
(ADR-024's public/private/external `ScopeOrigin` boundary — not redefined
here, only exercised at the merge layer once Phase B/C exist).

## Fixture 4 — scope drift

`scope_drift/old/` and `scope_drift/new/` declare the identical `a.h`/`b.h`
pair (byte-for-byte); `new/` additionally declares `c.h`, one extra TU with
no counterpart on the old side — a manifest/CLI-flag drift between two
extraction runs, not a real API change. `abicheck.comparability.compute_extraction_contract`/
`check_contracts_comparable` (ADR-050 D1/D2, G32 Phase A) exist and are unit
tested against exactly this shape of drift in `tests/test_comparability.py`,
and `checker.compare` itself now calls the gate — comparing `old/` against
`new/` through `compute_extraction_contract` + `compare()` hard-fails
`ScopeMismatchError` (`not_comparable`) by default, and
`compare(..., diagnostic_comparison=True)` downgrades that to a tentative,
`assurance: "none"`-stamped diff. `dumper.py` now calls
`compute_extraction_contract` on every real dump too, so a fresh
`dump`/`compare` invocation with fingerprintable extraction inputs (headers
given, or an L2 frontend ran) carries a real `contract`, not `contract=None`
— a plain binary/symbols-only dump with no headers still gets
`contract=None`, per `compute_extraction_contract`'s own "nothing to
fingerprint" rule. **Reachable today from all seven ADR-050 D2 entry
points**: the native `abicheck compare` CLI command (`--diagnostic-comparison`
flag, `verdict: null` JSON report, exit `16`), `cli_compare_release.py`'s
release fan-out (a per-library `"not_comparable"` verdict dominating the
rollup, exit `16`, though it does not itself accept
`--diagnostic-comparison`), `compat/cli.py`'s `compat check` (exit `9`),
`cli_scan.py`'s `scan --against` (`NOT_COMPARABLE` verdict, exit `6`),
`stack_checker.py`'s `deps compare` (`not_comparable_reason`, exit `5`), the
`abi_compare` MCP tool (`{"status": "not_comparable", ...}`, plus its own
`diagnostic_comparison` parameter), and `service.py`'s
`CompareRequest`/`run_compare_request`/legacy `run_compare` shim (threads
`diagnostic_comparison` through to whichever front-end called it).
`snapshot_cache.py`'s cache-key order-sensitivity, and SARIF/JUnit
rendering of a `not_comparable` outcome (native `compare` and
`compare-release`'s own JUnit report), and `aggregate.py`'s multi-target
fan-in gate (a `not_comparable` per-target report now blocks unconditionally
instead of decaying into a plain "unavailable" coverage gap) are also done
(G32 Phase A, complete). **Still not wired**: the legacy-CLI labeled
`--include old:LABEL=PATH` grammar is wired for native `compare` only (not
`scan --against`'s own separate `--include` registration, nor `dump`'s),
`compare-release`'s fan-out doesn't accept `--diagnostic-comparison`, and
`html_report.py`/`action/run.sh` render nothing for `not_comparable`
(a deliberate, documented gap — see below). See
`abicheck/comparability.py`'s own module docstring for the exact remaining
scope.

G32 Phase B (ADR-050 D3 — the manifest schema and real multi-TU dump) is
next; these Phase 0 fixtures (especially Fixture 3, external STL noise) are
what Phase B's own tests wire through the manifest path end to end.
