---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# L4 SYCL replay via a resolved `--gcc-path icpx`/`dpcpp` override — flag vocabulary fixed, the two-pass JSON-document crash fixed, real host+device dual-context replay still not implemented

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Fixed: -fsycl flag vocabulary (buildsource/adapters/base.py:160) and the two-pass JSON crash. Still open, as the entry says: host+device dual-context L4 replay.

Fixing L4 clang_bin
resolution to honor `--gcc-path` (an earlier PR) meant L4 could for the
first time actually invoke a SYCL-capable compiler (`icpx`/`dpcpp`)
instead of always a bare `clang`, which surfaced a narrower, real gap:
`-fsycl`/`-fsycl-*` wasn't in `adapters.base.ABI_RELEVANT_FLAG_PREFIXES`,
so it never reached the reconstructed L4 replay command even when the
real build recorded it (Codex review) — fixed, since the existing
`abi_relevant_flags` carry-through (`replay_extra_flags`) already handles
this class of flag correctly for every other case (`-std=`,
`-fvisibility`, …), so this was a one-line vocabulary gap, not a design
gap. That fix's own consequence #1 — "whether replaying without an
explicit `-fsycl-host-only` pin causes `icpx` to attempt a device pass
this pipeline can't consume" — was later **confirmed against a real
toolchain**: a real `-fsycl` bazel compile unit (oneDAL, 2.8GB AST) hit
exactly this, `json.load()` failing with `Extra data: line 26076735
column 2` at the byte offset where the device pass's document begins,
i.e. consequence #2 also confirmed (legacy `dpcpp`/`icpx` both emit
multi-document host+device AST output for a plain `-fsycl`, the identical
shape `sycl_context.py` already handles for the *L2* header-AST backend).
**Now fixed** by mirroring the L2 backend's own fix
(`dumper_ast_config._build_clang_header_command`/
`dumper_clang._needs_sycl_host_only`) rather than adopting
`sycl_context.py`'s full host/device dual-context decoder: L4 replay
parses ONE compile unit at a time and has no `--frontend-context device`
concept to select against (unlike L2's header-AST dump, which can be
asked for either context), so there is nothing for a second document to
serve here — `_clang_context_args` (shared by both the AST pass and the
macro pass) now appends `-fsycl-host-only` whenever
`dumper_clang._needs_sycl_host_only` says the resolved compiler is an
Intel oneAPI driver with SYCL effectively enabled and no single pass
already pinned, collapsing the compile back to the one host-side pass
that actually links into the scanned `.so` — the device pass's SPIR-V
kernel code never does. Reuses `_needs_sycl_host_only` directly (not a
reimplementation) so the last-flag-wins `-fsycl`/`-fno-sycl` scan and the
legacy `dpcpp`/`dpcpp-cl` SYCL-implied-by-default handling stay a single
source of truth with the L2 fix. The remaining "Extra data" `json.load()`
path now also emits an actionable hint (mirroring
`dumper_clang_errors._parse_clang_ast_result`'s) for any *other*,
not-yet-special-cased offload flag that still produces a multi-document
stream, rather than a bare byte offset. **Still not implemented, and
deliberately out of scope**: a genuine host+device dual-context L4
replay (an L4 counterpart to `--frontend-context device`) — nothing in
L4's `SourceAbiTu`/`CompileUnit` model has a notion of "this TU's device
pass," so adding one is a real, separate design (schema + linker +
cross-check changes), not a follow-up to this crash fix.
