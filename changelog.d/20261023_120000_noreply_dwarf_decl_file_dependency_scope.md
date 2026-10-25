### Fixed

- **A DWARF 5 declaration's file is now read** — the declaring file of a DWARF
  declaration is resolved for DWARF 4 and DWARF 5 line tables. The resolver
  read a DWARF 4 `name` entry for both, so every DWARF 5 declaration (GCC's
  default) recorded no declaring file. Declarations now carry their full
  declaring path and line (`<dir>/<file>:<line>`). This is an extractor
  change: the extractor generation is 19 (generations 13 to 18 belong to other changes already on main).
