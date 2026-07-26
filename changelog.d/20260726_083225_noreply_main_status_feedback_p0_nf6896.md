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
  reach a compiler's plugin/response-file/spec-substitution machinery**
  (`-Xclang`, `-load`, `-fplugin=`, `-fpass-plugin=`, `-specs=`,
  `-wrapper`, `@response-file`). The existing whitespace-smuggling check
  only rejected one YAML scalar expanding into multiple argv tokens; each
  of these is a single, whitespace-free atom and passed through untouched,
  even though this field is documented as a normalized ABI-flag escape
  hatch for untrusted, auto-discovered config — never executable
  configuration.
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
