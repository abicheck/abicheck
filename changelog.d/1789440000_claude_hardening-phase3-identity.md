### Changed

- **Platform name decorations are decoded by one codec each.** Mach-O's
  leading `_`, PE `__cdecl`/`__stdcall`/`__fastcall`/`__vectorcall`
  decoration, Itanium ctor/dtor variants (`C1`/`C2`/`C3`/`D0`/`D1`/`D2`) and
  ELF `@VER`/`@@VER` suffixes now live in `abicheck/model/name_decoration/`,
  with round-trip and injectivity property tests; the export join, rename
  detection, special-member resolution and the L5 graphs read their output
  instead of stripping decorations locally. The two PE decoders that
  disagreed are now one: a `__vectorcall` export (`name@@N`) joins its
  declaration on every machine, not only on 32-bit x86, and a stdcall/fastcall
  `@N` that is not a whole number of 4-byte slots is no longer decoded on the
  export-table-only path either. The PE extractor now stores each export's
  decoded C name in the snapshot (`PeExport.decoded_name`, written only
  for a decorated export); a snapshot written before this decodes it once
  when loaded.
- **A path reaches an identity key only root-relative.** Identity functions
  take a `RootRelativePath` (`abicheck/model/root_relative_path.py`) instead
  of a string, so an absolute path is a type error there. A recorded
  location is anchored at its project layout (`include`/`inc`/`src`/
  `source`/`sources`); an absolute location with no such anchor no longer
  contributes to a finding's `relsrc:` alias or its reduced-tier identity,
  so those stay equal across checkouts, symlinked roots, paths with spaces
  and Windows separators.

### Removed

- Internal helpers superseded by the codecs: `graph_entity_identity.
  pe_c_decoration_base`, `mangled_name.strip_macho_itanium_decoration` and
  `itanium_ctor_dtor_marker_span`, `finding_identity.source_relative_identity`
  (use `model.entity_identity.source_relative_identity`), the unused
  `identity_tiers.resolve_identity`, and `graph_reconcile`'s private
  `_project_relative_path`. `toolchain_probe.check_profile_toolchain_identity`
  is renamed `check_profile_toolchain_constraints` (it checks constraints; it
  is not an identity).
