### Fixed

- **clang header backend: static data member access** — the clang backend now
  records a variable's member access (a static data member declared under
  `private:`/`protected:`, or in a `class` before any specifier), as castxml
  does, and `var_access_changed`/`var_access_widened` now run on clang
  snapshots. Before, every clang variable read `public`, so narrowing or
  widening a static member's access reported no change under
  `--ast-frontend clang`. A clang snapshot from before extractor generation 54
  carries no access and is not compared for it. `EXTRACTOR_GENERATION` 54,
  snapshot cache salt v48.
