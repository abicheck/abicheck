### Changed

- **`buildsource/project_targets.py` is split into three owned modules** — the
  `targets:`/`bundles:`/`profiles:` validator (`validate_project_targets` and
  `ProjectTargetsValidationReport`) moves to
  `abicheck/buildsource/project_targets_validation.py`, the compile-option
  denylist and shlex-safety check to `project_compile_arg_guard.py`
  (`_safe_profile_atom` is now `safe_profile_atom`), and the runner-label and
  `dependency_source` routing tables to `project_profile_routing.py`. The
  parser and schema dataclasses stay in `project_targets.py`. No public
  behavior, error message, or config schema changes; importers are updated
  directly, with no re-export shim.
