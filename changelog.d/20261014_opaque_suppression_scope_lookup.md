### Fixed

- Opaque-type suppression: a bare by-value reference in another namespace no longer marks an unrelated opaque type as exposed when the snapshot declares the same-named type that C++ lookup finds first, and an opaque-struct forward declaration is no longer treated as absent when the other side defines it under its other spelling (`name` vs `qualified_name`, as DWARF and header producers differ), which used to suppress that type's layout findings.
