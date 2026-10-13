---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `--lang c++` explicitness not threaded to remaining fan-out callers (l0_export_delta/appcompat); dump/compare fixed

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Closed for dump/compare CLI and the DumpRequest/CompareRequest API. `lang_explicit` is threaded through frontends/cli/commands/dump.py, compare.py, workflows/dump/{native,pe,macho}.py and workflows/artifact/*, and release_member_request.py now carries it too. The scan follow-up is moot (scan was deleted). appcompat and l0_export_delta are unverified. l0_export_delta.py takes a plain `lang: str` with no lang_explicit, so that follow-up looks still open.


`cli_dump_helpers.py`'s ELF `dump` path (and `service.py`'s two mirrors,
`_dump_elf`/PE-Mach-O's `_header_graph_lang`) all normalize the caller's
`lang` to `lang if lang == "c" else None` before calling `dumper.dump()` —
deliberately, per their own comment: "every format's own main pass
normalizes `lang` to only ever force a language explicitly requested,
letting auto-detection run otherwise (including for the default 'c++')",
so `_attach_header_graph`'s own `_clang_header_dump` call computes the
*identical* cache key and hits the in-process AST memo instead of paying a
second clang invocation. That assumption — "an explicit `--lang c++` and
auto-detection converge on the same result anyway, so unifying their cache
keys is free" — is false whenever the header itself is language-ambiguous:
`_resolve_force_cpp`'s auto-detection (`_detect_cpp_headers`/
`_detect_cpp20_headers`/an explicit `-std=c++NN`) requires the header to
contain *some* C++-only syntax, but a plain POD struct with no such syntax
(`struct Widget { int x; int y; };` — ordinary, real C++ code, e.g. a
value/DTO type) compiles as valid C too and auto-detects as C. Reproduced
end-to-end: `abicheck dump lib.so -H widget.h --ast-frontend clang --lang
c++` silently parses `widget.h` in **C mode** for the primary snapshot
(confirmed via the raw clang AST: a plain `RecordDecl`, not
`CXXRecordDecl`, no `definitionData` at all) — with no error, warning, or
any user-visible sign the explicit `--lang c++` was overridden — while the
*same* `dump`'s internal `_attach_header_graph` pass, reached through a
different `lang` derivation one call removed
(`abicheck/service.py:1281`/`abicheck/service.py:608`'s ELF branch shares
the identical `lang if lang == "c" else None` squash, so it too loses the
signal — the divergence traced here is specifically the ELF `_dump_elf`
helper's own second, ADR-050/G31-era call site,
`abicheck/cli_dump_helpers.py:1717`, versus `_clang_header_dump`'s
documented "an explicit `--lang c++`/`cpp` always wins" contract at the
function it's calling into — the squash happens one layer *above* that
contract, defeating it before it ever runs). Concrete, silent correctness
cost: `RecordType.is_standard_layout`/`is_trivially_copyable` (and any
other clang-only, C++-semantic-only fact — `_clang_record_type_traits`
itself documents "a plain C `RecordDecl`... genuinely absent", the correct
behavior *for C mode*, just not the mode the user asked for) silently read
`None` instead of a real value for a header that would parse identically
either way *except* for these C++-only facts, with the user's own explicit
override having no effect. **Not fixed here**: the squashing is not an
oversight — it is a deliberate, twice-Codex-reviewed cache/memo-consistency
design (see `abicheck/service.py:608`'s own comment) that this finding does
not invalidate for the *common* case (a header with real C++ syntax
auto-detects as C++ regardless, so squashing costs nothing there) — only
for the specific, real, silently-wrong case: an explicit `--lang c++` on a
syntactically-ambiguous header. A correct fix needs the squash itself to
stop conflating "auto-detect, and it happens to reach the same verdict as
the default" with "explicitly override, must not be silently downgraded to
a guess" — e.g. resolving `force_cpp` once, upstream of all three call
sites and their `_attach_header_graph` mirrors, and threading the
*resolved* boolean (or an unsquashed `lang` plus a corrected memo key that
hashes the resolved language rather than the raw flag) through consistently
— a change to a shared cache-key contract three call sites and two
Codex-reviewed comments currently rely on, not a one-line fix at any single
site. No test in the repository currently exercises this path: every
existing `is_standard_layout`/`is_trivially_copyable` regression test
(`tests/test_dumper_clang.py::test_parse_types_populates_standard_layout_and_trivially_copyable`,
`tests/test_diff_layout.py`) hand-builds the parsed AST or `RecordType`
directly, bypassing `dumper.dump()`'s CLI-level `--lang` resolution
entirely — closing this gap should add an end-to-end regression case in
the shape of `tests/test_clang_header_backend_integration.py`'s existing
siblings, verified against a real compiled library with an
intentionally-C-compatible C++ struct, once the fix itself is designed.

**Closed for the `abicheck dump` ELF CLI path — scoped narrower than the
"resolve `force_cpp` once, upstream of all three call sites" design sketch
above (G31 continuation).** Rather than a shared, force_cpp-boolean
cache-key contract spanning `cli_dump_helpers.py`, `service.py`'s
`_dump_elf`, and `service.py`'s PE/Mach-O `_header_graph_lang`, the actual
fix is narrower: only `cli_dump_helpers.py`'s ELF `dump` CLI path had a
real *internal* divergence between its own primary pass (squashed) and its
own `_attach_header_graph` call (raw, unsquashed) — `service.py`'s
`_dump_elf` and its `_header_graph_lang` computation already squash
*consistently* with each other (both feed the identical normalized value),
so `compare`'s implicit-dump path and the Python `service.run_dump` API
were never the site of this specific divergence; PE/Mach-O's own primary
pass (`_try_header_scoped_dump`) never squashed at all. What was missing
everywhere is the one bit no string-normalization scheme can recover on
its own: whether `--lang` was genuinely given on the command line, since
Click's own default for `--lang` (`LANG_DEFAULT`, `cli_options.py`) is the
identical string `"c++"` a real `--lang c++` produces. `dump_cmd` now
resolves this once via Click's own parameter-source tracking
(`click.get_current_context().get_parameter_source("lang") ==
click.core.ParameterSource.COMMANDLINE`) and threads a new
`lang_explicit: bool` keyword parameter through `perform_elf_dump`, which
derives one `_effective_lang` (the real `lang` when explicit or `"c"`,
else `None`) and passes that identical value to *both* the primary
`dump()` call and the `_attach_header_graph` call, instead of the primary
pass's own one-off squash and the graph pass's raw pass-through. This also
fixes a second, previously-undocumented half of the same divergence in the
*other* direction: on a plain default invocation (no `--lang`, still
`lang="c++"`), the header-graph pass previously force-parsed C++
unconditionally (since a non-empty `lang` was always treated as explicit
by `_resolve_force_cpp`), while the primary pass correctly auto-detected —
so even a default, no-flags `dump --ast-frontend clang` could already
silently disagree with itself between its own primary snapshot and its
own embedded header-graph. Verified end-to-end against a real compiled
library with an intentionally-C-compatible POD struct (`struct Widget {
int x; int y; };`, exactly this entry's own repro shape) through the real
`abicheck dump` CLI, not a hand-built AST or `RecordType` — see
`tests/test_clang_header_backend_integration.py::
test_cli_dump_explicit_lang_cpp_forces_cpp_mode_on_ambiguous_header`.
**Closed for `service.run_dump`/`DumpRequest`/`CompareRequest` in a later
pass, once a real conda-forge castxml build (0.7.0, within the
`>=0.6.11,<0.8.0` policy range — `castxml_policy.py`) was available in this
environment to verify against, alongside clang 18.** Rather than the
`lang: str | None = None` tri-state default this entry originally
sketched — a public-API *shape* change with a wide, hard-to-verify blast
radius across every `resolve_input`/`run_dump` caller (`compare`, `scan`,
`appcompat`, `l0_export_delta`, ...), most of which still legitimately pass
a concrete, Click-defaulted `lang` string that must keep auto-detecting —
the actual fix is the same **additive** `lang_explicit: bool = False`
parameter the CLI fix above already established, generalized one layer
up: `service.resolve_input`/`run_dump`/`_dump_elf`/`_dump_pe`/
`_dump_macho` and `service_header_scoped._try_header_scoped_dump` all gain
it (default `False`, a no-op — every existing caller's behavior is
bit-for-bit unchanged), `DumpRequest.lang_explicit`/
`CompareRequest.lang_explicit` carry it on the typed API, and
`service_dump_pipeline.run_dump_request`/`service_compare_pipeline.
resolve_compare_request` thread it through `resolve_side_snapshot` (their
one shared per-side resolution function) to `service.resolve_input`. The
`dump`/`compare` CLIs resolve it the identical way `dump_cmd` already did
— `compare_cmd` mirrors the established `_frontend_explicit`/
`_nostdinc_explicit` `ctx.get_parameter_source(...)==COMMANDLINE` pattern
already used one function over in `cli_compare_helpers._embed_inline_
source_side` for `--ast-frontend`/`--nostdinc`, extended to `--lang` and
threaded through `cli_resolve._resolve_compare_snapshots` into
`CompareRequest`. The whole-snapshot disk cache key
(`workflows/dump/cache/key.py`'s `_dump_cache_extra_key`/`api.py`'s `cached_run_dump`) folds
`lang_explicit` in too — the identical `lang` string now legitimately
resolves to two different parsed ASTs depending on it, so a cache entry
for one must never serve the other. `scan`/`appcompat`/the
release/set-input fan-out/`l0_export_delta` are **not** touched by this
pass — they still pass their own already-resolved, Click-defaulted `lang`
string with `lang_explicit` defaulted `False`, so they keep their
pre-existing (unfixed, but also not regressed) behavior; wiring each is
the identical mechanical pattern applied here, left for its own follow-up
rather than expanding this pass's verified surface further. Verified
end-to-end against the same real, intentionally-C-compatible POD struct
through `service.resolve_input`, `DumpRequest`/`run_dump_request`,
`CompareRequest`/`resolve_compare_request`, and the real `compare` CLI
(Click parameter-source spy) — see
`tests/test_clang_header_backend_integration.py::
test_dump_request_and_compare_request_lang_explicit_forces_cpp_mode` and
`tests/test_service_dump_cache.py`'s
`test_differs_by_lang_explicit`/`test_lang_explicit_reaches_run_dump_and_keys_separately`.
