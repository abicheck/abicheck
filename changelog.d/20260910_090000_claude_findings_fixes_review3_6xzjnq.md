### Fixed

- **`resolved_config.policy.overrides`/`effective_config_fields["policy.
  overrides"]` (`--contract auto --format json`) could disagree with what
  actually scored the run.** A project-config override
  (`.abicheck.yml`'s `policy.overrides`) reached the scoring `PolicyFile`
  through a separate, out-of-band merge that ran *after* the canonical
  ADR-049 D7 resolver had already produced its receipt, so a project
  override that genuinely made a finding compatible still left the receipt
  showing `{}`/no contributor for that kind. Fixed generally: the canonical
  resolver (`compatibility_evaluation_frontend.py`) now folds
  `.abicheck.yml`'s `policy.overrides` in itself, at the weakest
  (`project_config`) D7 precedence tier, with real provenance naming it —
  so the receipt and the value that scored the run can no longer drift
  apart.
- **`scan --against` silently ignored `.abicheck.yml`'s `policy.overrides`,
  the documented replacement for the retired `--crosscheck KEY=LEVEL`
  flag.** An identical snapshot pair with `policy.overrides.func_removed:
  ignore` exited `0` under `compare` but `4` under `scan --against` — the
  project config was parsed but never folded into the `PolicyFile`
  `cli_scan.py` builds for the baseline comparison. Fixed by threading the
  same `PROJECT_CONFIG`-tier fold `compare` and the directory/package
  release fan-out already apply, at the identical point (strictly after any
  `--pack` fold, so an explicit pack override still outranks it).
- **`summary.compatible_additions`'s meaning changed under a MINOR schema
  bump.** A prior fix (report_schema_version 3.15) narrowed this required
  field from "every `COMPATIBLE` finding" to "only genuine additions"
  without bumping the schema's MAJOR version — a real breaking change per
  this schema's own documented policy (a required field's *meaning*
  changing, not just a key being added/removed/retyped), which left a
  consumer written for an earlier 3.x silently undercounting a
  quality-only or mixed run under the new 3.15 semantics it never
  requested. Fixed by renumbering to `4.0` with a condensed history entry
  recording the correction; `compatible_additions`'s current semantics
  (added in the original fix) are unchanged — only the version number that
  announces the change is now correct.
- **A namespace *component* pulled out of a templated `_ZTV`/`_ZTI`/`_ZTT`
  vtable/RTTI/VTT owner's mangled scope path could stand in as its own
  implicated-type candidate during public-surface classification.**
  `itanium_special_name_owner_identifiers` flattened every identifier
  anywhere in the owner's scope path and template-argument list(s) into one
  undifferentiated set — `_ZTVN2ns7WrapperIiEE` (`ns::Wrapper<int>`)
  produced `{"ns", "Wrapper"}`. If the snapshot modeled an unrelated,
  unreachable type literally named `ns` but had no `ns::Wrapper<int>` of
  its own, `surface.py`'s reachability classifier could resolve the
  unrelated `ns` as the finding's only known candidate and confidently
  demote it as `non-public-type`, even though the *real* owner was
  genuinely unresolvable from the available evidence (whose conservative
  default is "unknown, keep" — never hide a real break). Fixed generally:
  the mangled-name parser now distinguishes a scope-path component from a
  template-argument-embedded identifier structurally, fusing every
  qualified name (at any nesting depth, including inside nested template
  arguments) the same way `surface.py`'s own `_type_identifiers` already
  treats an ordinary demangled type string — only a fully qualified owner
  and its own bare tail are ever emitted as standalone candidates; a bare
  namespace segment never is.

### Changed

- `abicheck/model/mangled_name.py`'s length-prefixed-name/template-skip
  primitives and its new recursive template-argument-identifier extraction
  moved to a new sibling leaf module,
  `abicheck/model/mangled_name_template_args.py`, keeping the parent module
  under the AI-readiness production file-size cap without an
  `architecture/debt.yaml` baseline entry.
