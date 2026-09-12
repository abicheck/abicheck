### Fixed

- **A declaration that is still in the public headers is no longer reported as
  a source removal when the binary stops exporting it.** `Visibility.PUBLIC`
  conflated two independent facts — "declared in a parsed header" and
  "dynamically exported" — so every `visibility == Visibility.PUBLIC` guard
  answered whichever question its author meant and the other one by accident.
  A public inline method whose declaration was byte-identical across two
  builds, and which differed only in whether it was emitted as a dynamic
  export, dropped out of `diff_namespaces`' source-surface index and was
  reported as `EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT`. `Function` and
  `Variable` now carry the two facts apart as `declared_fact`/`exported_fact`
  (`Fact[bool]`, snapshot schema v46), read through the new
  `model/declaration_surface.py`; a snapshot written before v46 carries
  neither, loads them as "no evidence" rather than `False`, and produces
  exactly the findings it did before.

### Added

- **`FUNC_EXPORT_REMOVED_STILL_DECLARED` / `VAR_EXPORT_REMOVED_STILL_DECLARED`
  (BREAKING)** — the export axis of the same split, emitted in place of
  `FUNC_REMOVED`/`VAR_REMOVED` when the binary stopped exporting a symbol the
  public headers still declare. Still an ABI break, because an already-linked
  consumer resolves that symbol at load time; the change is that the report no
  longer claims the declaration was removed from the API when it was not.
