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

### Documentation

- **`README.md` no longer claims the conda-forge `abicheck` feedstock
  bundles a C/C++ compiler as a run dependency**, and now calls out that
  its `castxml >=0.6.3` floor is looser than abicheck's own `>=0.6.11`
  version gate. Also corrected the legacy PyPI `castxml` package's last
  release date (0.4.5 shipped September 2022, not 2018) here and in
  `castxml_policy.py`'s docstring.
