### Fixed

- **`--compile-db-filter` could silently match every compile unit instead of
  narrowing, when a `compile_commands.json` entry's `directory` was relative
  and the caller supplied the (spec-correct, naturally-typed) absolute
  filter.** Real `compile_commands.json` entries always give `directory`
  absolute per the Clang compilation-database spec, but `source_matches_
  filter()` never enforced that, and a caller could hand it a relative one.
  The joined candidate then stayed relative too, so it could never match an
  absolute pattern — while `CompileEntry.from_dict()` itself resolves the
  same file to an absolute path before the L2/legacy layers inspect it, so
  those layers correctly narrowed to the requested translation unit while
  this raw scan fell back to "every entry matches," letting disagreeing
  `-D`/`-I` flags from an unrelated TU be intersected into the resolved
  compile context. Fixed by also testing the CWD-resolved absolute form of
  any relative candidate, alongside the existing readings — the same
  "widen, never narrow" approach this function's docstring already commits
  to for its other candidate-shape fixes.
