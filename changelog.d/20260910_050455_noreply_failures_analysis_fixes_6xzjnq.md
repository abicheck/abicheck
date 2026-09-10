### Fixed

- **Public-surface scoping no longer falls back to the optional external
  demangler for a *templated* vtable/RTTI/VTT owner.** The dependency-free
  structural parser `itanium_special_name_owner_scope_components` already
  closed this reproducibility defect for a non-templated owner, but the
  templated case (e.g. a libstdc++ container instantiation — oneDNN's real-
  world shape) still fell back to `demangle()`, so the exact same comparison
  could classify differently (and demote a different `exported_not_public`
  count out of contract) depending only on whether `cxxfilt`/`c++filt`
  happened to be installed on the host — a policy-affecting classification
  must not vary by host. A new structural extractor,
  `model.mangled_name.itanium_special_name_owner_identifiers`, pulls every
  class/namespace identifier out of the owner's scope path *and* its
  template-argument list (at any nesting depth) without an external tool;
  `surface.py`'s type-candidate resolution now uses it unconditionally for
  every `_ZTV`/`_ZTI`/`_ZTT` owner, templated or not. Verified: the
  classification and contract decision are identical whether `demangle()`
  is mocked present or absent, across several representative libstdc++- and
  user-templated owner shapes; `Verdict`/exit code were already unaffected
  by this specific fallback in every case investigated.
- **`--policy` YAML documents now reject an unrecognized top-level key**
  (`suppress:` where the real key is `overrides:`/`reclassify:`, or any
  other typo/misspelling) as a hard `PolicyError`, instead of silently
  parsing to a no-op policy with no warning, no error, and an unchanged
  exit code. Symmetric with ADR-049 D8's existing hard error for an unknown
  `ChangeKind` slug *inside* `overrides:`. Reuses
  `policy_file_versioning._reject_unknown_keys`, the same unknown-key
  convention the `versioning:`/`support_window:`/`deprecation_window:`
  sub-blocks already enforce.
- **`.abicheck.yml`'s documented `policy.overrides` route now exists.**
  ADR-068 §3 #23 named `--policy`/`.abicheck.yml`'s `policy.overrides` as
  the replacement for the retired `--crosscheck KEY=LEVEL` flag, but a real
  `.abicheck.yml` carrying a top-level `policy:` key failed outright with
  `Error: unknown .abicheck.yml key 'policy'` — the documented interface
  did not exist. `BuildConfig` now accepts `policy.overrides` (a
  `ChangeKind` slug -> severity mapping, validated with the identical
  `--policy <file>`-shared `overrides:` schema), and `compare` folds it
  into the run's effective policy at the `project_config` precedence
  tier: an explicit `--policy <file>`'s own override for a given kind
  still always wins; a kind only the project config states is applied
  even with no `--policy <file>` given at all.
- **`summary.compatible_additions` no longer double-counts a quality-only
  finding as a compatible addition.** `summary.quality_issues` (schema
  3.13) was added specifically to *name* the non-addition subset (e.g. a
  `public_surface_shrank` finding, a net public-surface *decrease*)
  polluting `summary.compatible_additions`, but left that field itself
  still counting every `COMPATIBLE` finding — so a consumer reading
  `compatible_additions` alone still read a shrink as growth.
  `compatible_additions` (report schema **3.15**) now counts only genuine
  `ADDITION_KINDS` findings; `compatible_additions + quality_issues` equals
  the old (<=3.14) `compatible_additions` total, so an existing consumer of
  the old field can reconstruct it from the new pair. A double-subtraction
  this uncovered in the Markdown review digest's own "Additions" row
  (`reporter_markdown.py`, which used to subtract `quality_issues` a
  second time) is fixed alongside it.
- **The Markdown report (`--format markdown`, `--report-mode
  leaf`/`root-cause`) now ends with a trailing newline**, matching the
  review-digest format's pre-existing `.rstrip() + "\n"` convention and
  POSIX text-file convention — `render_markdown_document.
  render_markdown_document` and `render_markdown_alternate`'s
  `render_leaf_document`/`render_root_cause_document` previously joined
  their rendered lines with no trailing newline at all.
