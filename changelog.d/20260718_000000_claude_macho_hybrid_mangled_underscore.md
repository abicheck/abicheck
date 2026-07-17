<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`--ast-frontend hybrid`** on Mach-O: clang's `-ast-dump=json`
  `mangledName` carries the extra Darwin linker-symbol leading underscore
  (`__ZN...`) while castxml's own `mangled` is prefix-free (`_ZN...`) for
  the identical function/variable. The hybrid merge's dedup check never
  saw these as the same declaration, so every Mach-O C++ function and
  variable was duplicated (once via castxml, once via a spurious
  "clang-only" entry) and ctor/dtor reconciliation never even recognized
  the double-underscore form as Itanium-mangled. clang's function/variable
  mangled names are now normalized to castxml's convention before the
  merge on Mach-O.
