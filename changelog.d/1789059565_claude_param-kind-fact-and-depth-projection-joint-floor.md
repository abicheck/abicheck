### Fixed

- **`compare` no longer fabricates `FUNC_PARAMS_CHANGED` when a
  header-derived snapshot is compared against a DWARF-derived one of the
  identical, unchanged library.** Neither header-AST backend (castxml,
  clang) had ever determined a parameter's indirection kind
  (value/pointer/reference/rvalue-reference); every parameter silently
  read the model's own resting `ParamKind.VALUE`, and `diff_symbols`
  compared that raw default directly against DWARF's real reading (always
  a genuine producer) with no producer gate — a false break on every
  pointer/reference parameter whenever the two evidence families were
  mixed. Both header-AST backends now genuinely determine a parameter's
  kind (castxml structurally, from its own type graph; clang via a
  documented spelling heuristic), and `Param.kind_fact` (schema v45,
  `AbiSnapshot.param_kind_facts_reliable` guards a persisted pre-v45
  snapshot's placeholder reading) lets the detector decline a comparison
  it cannot trust instead of manufacturing one.
- **`--depth binary`/`--depth headers` no longer manufacture findings when
  the two sides of a comparison reached different structural-evidence
  tiers.** `project_pair_to_depth` used to project each side's evidence
  down to the requested rung independently, so a header-derived side
  (stripped of `types`/`enums`/`typedefs`/function signatures) compared
  against a DWARF-derived side of the *identical* library (which kept all
  of that, DWARF being a genuine producer) read every real fact the richer
  side still carried as a fabricated addition — worse than not projecting
  at all. The projection now computes one joint floor for both sides. A
  sibling fix in the same pass: `typedefs_qualified`/`typedef_entity_ids`/
  `constant_entity_ids` — identity sidecars keyed exactly like
  `typedefs`/`constants` — were never cleared alongside their partner
  dicts at all, so even a solo, correctly-symmetric projection still left
  a residual, fully-manufactured `TYPEDEF_REMOVED`/`TYPEDEF_ADDED`
  standing.
- **The joint depth-projection floor above no longer leaks private DWARF
  types into `--depth binary`'s layout diff.** Clearing model `types` on
  *both* sides of a mixed-evidence pair also emptied
  `diff_platform._diff_dwarf`'s own scope derivation, which then fell back
  to comparing every raw DWARF struct/enum unscoped — a private,
  non-public struct with a genuinely differing internal layout could
  manufacture a breaking `STRUCT_SIZE_CHANGED` for two dumps of the
  identical library. `project_pair_to_depth` now pre-scopes the raw
  `dwarf.structs`/`.enums` pool to the public names both sides knew about
  before projection.
- **A typedef'd reference/rvalue-reference parameter (`typedef int &Ref;`)
  no longer disagrees across producers.** castxml's structural type-graph
  walk already unwrapped a typedef to find the real underlying kind, but
  clang's spelling heuristic saw only the bare alias name (no `&`/`&&`
  token to find) and `dwarf_snapshot.py`'s reference-type check inspected
  only the immediate, un-unwrapped `DW_AT_type` target — both silently
  read `VALUE` instead of the real kind. clang now reads clang's own
  `desugaredQualType` (`context.qualtype_desugared`) and DWARF now unwraps
  the same const/volatile/typedef chain its pointer-depth count already
  does (`dwarf_utils.unwrap_cv_typedef`).
