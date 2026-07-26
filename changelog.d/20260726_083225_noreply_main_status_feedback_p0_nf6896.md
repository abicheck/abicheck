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
- **`run-plan generate`'s composed `compile_gcc_options` no longer emits
  `-stdlib=`/`--target=` for a profile declaring `compile.compiler_family:
  gcc`.** Both are Clang-driver-only spellings a real GCC binary rejects
  (confirmed against GCC 14.2). `compiler_family: clang` and an unset
  `compiler_family` (the pre-existing default, still consumed by castxml's
  own Clang-based emulation frontend either way) are unaffected.
- **Fixed a correctness gap the GCC-family fix above itself introduced:** a
  GCC profile setting only `stdlib`/`target` (both dropped by the filter,
  nothing else configured) used to compose to plain `""`, indistinguishable
  downstream from "no `compile:` overlay at all." `check-project.yml`'s
  matrix step does `gcc-options: ${{ matrix.compile_gcc_options ||
  inputs.gcc-options }}`, and GitHub Actions expression truthiness treats
  `""` the same as an absent property, so the empty result silently fell
  back to the workflow-global `gcc-options` — reintroducing the exact
  Clang-only flags this filtering exists to keep off a GCC cell, in a mixed
  GCC/Clang matrix. `_compose_gcc_options` now returns a single space
  instead in that specific case: truthy for the `||` fallback check, yet
  inert once actually used as argv (`shlex.split(" ") == []`).
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

### Documentation

- **`README.md` no longer claims the conda-forge `abicheck` feedstock
  bundles a C/C++ compiler as a run dependency**, and now calls out that
  its `castxml >=0.6.3` floor is looser than abicheck's own `>=0.6.11`
  version gate. Also corrected the legacy PyPI `castxml` package's last
  release date (0.4.5 shipped September 2022, not 2018) here and in
  `castxml_policy.py`'s docstring.
- **`docs/use/output-formats.md`'s `release_recommendation` JSON-gating
  example now checks `state` before `version_bump`**, and documents that
  `version_bump` is `null` when `state` is `"unavailable"` — the previous
  example gated on `version_bump` alone and showed only the always-present
  `"major"` case, which is no longer true after the honesty fix above.
- **`tests/scenarios/release_management.yaml`'s `SC-RELEASE-RECOMMENDATION`
  scenario's documented `expected`/narrative now match what
  `test_sc_release_recommendation` actually asserts** (`state: unavailable`,
  `version_bump: null` — this scenario compares hand-built snapshots with
  no binary evidence) instead of the stale `version_bump: major` the
  version_bump-honesty fix above left behind; the catalog's own structural
  check doesn't cross-validate `expected` values against the real
  assertions, so this had silently gone stale (Codex review).
