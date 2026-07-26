### Fixed

- **`release_recommendation.version_bump` is `null` instead of a
  plausible-looking `"major"` when `state` is `"unavailable"`.**
  `ReleaseRecommendation.to_dict()` previously always serialized the
  dataclass's `bump` value even when the accompanying `state` explicitly
  says abicheck had no real binary/coherence evidence to back it —
  automation reading `version_bump` without also checking `state` could act
  on a release bump abicheck itself could not confirm. The rationale prose
  still explains the still-plausible bump; only the machine-readable field
  changes (`compare_report.schema.json` schema 2.20, additive/relaxed —
  `version_bump`'s enum now includes `null`).
- **`profiles.<id>.compile.args` (`.abicheck.yml`) now rejects flags that
  reach a compiler's plugin/response-file/spec-substitution/subprocess-
  forwarding machinery** (`-Xclang`, `-load`, `-fplugin=`, `-fpass-plugin=`,
  `-specs=`/`--specs=`, `-wrapper`, `--config`, `@response-file`, and GCC's
  `-Wa,`/`-Wp,`/`-Wl,`/Clang's `-Xpreprocessor`/`-Xassembler`/`-Xlinker`
  subprocess-forwarding families). The existing whitespace-smuggling check
  only rejected one YAML scalar expanding into multiple argv tokens; each
  of these is a single, whitespace-free atom and passed through untouched,
  even though this field is documented as a normalized ABI-flag escape
  hatch for untrusted, auto-discovered config — never executable
  configuration. (`--config`, the `--specs` double-dash spelling, and the
  `-Wa,`/`-Wp,`/`-Wl,`/`-Xpreprocessor`/`-Xassembler`/`-Xlinker` family
  added across review: e.g. `-Wp,-fplugin=./evil.so` forwards straight to
  cc1 and loads the plugin exactly as a bare `-fplugin=` would, and
  `-Wl,-plugin=./evil.dso` loads an LTO linker plugin the same way — each
  could otherwise reintroduce a blocked argument past this same denylist.)
- **Every `profiles.<id>.compile.*` atom (`standard`/`stdlib`/`target`/
  `abi_macros`/`args`) now also rejects quote (`'`/`"`) and backslash (`\`)
  characters, not just whitespace.** `_compose_gcc_options` space-joins
  every atom from every field into one string, and the eventual consumer
  (`dumper.py`'s `--gcc-options` handling) re-splits that whole string with
  `shlex.split(..., posix=...)` to recover argv — an atom like
  `"'-fplugin=./evil.so'"` starts with a quote, not `-fplugin=`, so the
  denylist above alone would accept it, but POSIX shlex quote-removal
  reconstitutes the exact blocked flag once the composed string is
  re-split. Found and confirmed during review with a `shlex.split()`
  round-trip demonstration; regression-tested the same way.
- **`run-plan generate`'s composed `compile_gcc_options` briefly stopped
  emitting `-stdlib=`/`--target=` for `compile.compiler_family: gcc`
  profiles, then that change was reverted as incorrect.** The original
  reasoning (a real GCC binary rejects both, confirmed against GCC 14.2)
  was true but never applicable: this composed string is never actually
  fed to a literal GCC binary anywhere in this pipeline (every
  `--ast-frontend` — `castxml`/`clang`/`hybrid` — routes through Clang;
  there is no `gcc` frontend). Since the real consumer is always Clang,
  dropping `--target=` broke real cross-compilation-target correctness for
  the direct-clang backend, which has no other way to steer header parsing
  away from the host architecture. Reverted to unconditional emission
  regardless of `compiler_family`, with the full history recorded in
  `_compose_gcc_options`'s own docstring.
- **`profiles.<id>.compile.args` also rejects `--castxml-cc-` now.** A
  second `--castxml-cc-<id> <path>` occurrence appended after abicheck's
  own trusted pair might look like it could replace the verified compiler
  path with an attacker-controlled one; empirically verified against
  castxml 0.6.3 that this is not actually exploitable (castxml
  hard-rejects any repeated `--castxml-cc-*` occurrence at argv-parse
  time — `error: '--castxml-cc-<id>' may be given at most once!` — so the
  scan fails outright rather than silently invoking a substituted binary)
  but blocked anyway for defense-in-depth and a clearer abicheck-level
  error instead of relying on that castxml-internal invariant holding
  across every supported version.
- **`profiles.<id>.compile.args` also rejects `-B<dir>`/`-B <dir>` now.**
  GCC's `-B<dir>` really does add a directory to its compiler-component
  search path and really does execute an attacker-supplied `cc1`/`cc1plus`
  placed there (empirically confirmed: `gcc -B./tools/ -E` ran a planted
  `./tools/cc1`) — but empirically verified this does not reach abicheck's
  actual pipeline: every consumer of this composed string (castxml's
  internal bundled Clang, and the direct `--ast-frontend clang` backend)
  is Clang, not GCC, and Clang re-execs itself via `-cc1` instead of
  spawning a separate, `-B`-discoverable `cc1` (confirmed: `-B./tools/`
  did not run a planted `./tools/cc1` for either castxml or a direct
  `clang -E`). Blocked anyway: cheap, and closes the door in case a
  future toolchain-execution-contract change ever forwards these flags to
  a real GCC invocation directly.
- **`profiles.<id>.compile.args` also rejects clang-cl's `/clang:` and
  `/link` now.** `/clang:<arg>` forwards straight to the underlying clang
  driver, bypassing clang-cl's MSVC-shaped option parsing entirely —
  empirically confirmed exploitable (`clang --driver-mode=cl
  "/clang:-fplugin=./evil.so" -c t.h` loads and runs the planted plugin),
  and reachable through this pipeline via a `compile.binding` whose path
  stem contains "clang" (e.g. `clang-cl`/`clang-cl.exe`, recognized as
  clang-family by `dumper_clang._is_clang_family_binary`). `/link
  <options>` is clang-cl's own "forward to the linker" escape hatch — the
  cl-mode spelling of the already-blocked `-Wl,` mechanism — blocked for
  the same LTO-linker-plugin reason.
- **`profiles.<id>.compile.args` also rejects `-cc1`/`-cc1as` now.** Clang's
  internal `cc1`/`cc1as` frontend mode only activates when `-cc1`/`-cc1as`
  is literally the first argument after the program name (confirmed
  empirically), and `dumper.py`'s `_build_clang_header_command` builds
  argv as `[cc_bin, *-I dirs, --sysroot, -nostdinc, *gcc_options tokens,
  ...]` — a scan with no `extra_includes`/`sysroot`/`nostdinc` lets a
  leading `-cc1` genuinely land in that slot. Once in cc1 mode, the
  already-blocked `-load`/`-fpass-plugin=` still work, but cc1 exposes an
  entirely different, unenumerated argument namespace (Codex review found
  `-fcas-plugin-path`, a cc1-only flag, doing the identical thing in a
  Clang build that has it) — rejected the mode switch itself rather than
  chasing individual cc1-only flags.
- **`release_recommendation`'s JSON Schema now enforces the
  `version_bump`/`state` pairing it only documented in prose** (schema
  2.21, `compare_report.schema.json` and its `docs/reference/schemas/v1/`
  mirror): an `allOf`/`if`/`then` pair requires `version_bump: null` iff
  `state == "unavailable"`. 2.20 widened `version_bump`'s type to accept
  `null` but didn't tie that to `state` at the schema level, so a
  producer bug emitting a mismatched pair (e.g. a concrete bump alongside
  `state: "unavailable"`) would still validate; every real producer
  already only emits the paired combination, so this tightens validation
  without changing what a conformant report looks like (CodeRabbit
  review).

### Documentation

- **`README.md` no longer claims the conda-forge `abicheck` feedstock
  bundles a C/C++ compiler as a run dependency**, and now calls out that
  its `castxml >=0.6.3` floor is looser than abicheck's own `>=0.6.11`
  version gate. Also corrected the legacy PyPI `castxml` package's last
  release date (0.4.5 shipped September 2022, not 2018) here and in
  `castxml_policy.py`'s docstring. The recommended `conda create` command
  now pins `castxml>=0.6.11` directly instead of only mentioning the pin
  in prose (CodeRabbit review).
- **`docs/use/output-formats.md`'s `release_recommendation` JSON-gating
  example now checks `state` before `version_bump`**, and documents that
  `version_bump` is `null` when `state` is `"unavailable"` — the previous
  example gated on `version_bump` alone and showed only the always-present
  `"major"` case, which is no longer true after the honesty fix above. The
  example now branches with an explicit `if .state == "actionable"`/`elif
  "review"`/`else` chain instead of just formatting all three fields
  unconditionally, so it actually demonstrates the gating it describes
  (CodeRabbit review).
- **`tests/scenarios/release_management.yaml`'s `SC-RELEASE-RECOMMENDATION`
  scenario's documented `expected`/narrative now match what
  `test_sc_release_recommendation` actually asserts** (`state: unavailable`,
  `version_bump: null` — this scenario compares hand-built snapshots with
  no binary evidence) instead of the stale `version_bump: major` the
  version_bump-honesty fix above left behind; the catalog's own structural
  check doesn't cross-validate `expected` values against the real
  assertions, so this had silently gone stale (Codex review).
