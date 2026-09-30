# Diagnostics — commands and readings for this workflow

The exact commands behind the parent skill's steps, kept out of `SKILL.md`.
Reading a comparison report in general is
[report interpretation](../../shared/report-interpretation.md); this file
covers what is specific to explaining a change between the library a
program was built against and the one it actually uses.

## Preflight

```bash
abicheck --version
```

## Which library actually loads

Resolve the program's dependency tree the way the loader will, under the
environment the program really runs in. Pass the same library path its
launcher sets:

```bash
abicheck deps tree path/to/program --ld-library-path "$LD_LIBRARY_PATH" -o json=tree.json
```

In `tree.json`:

- `missing_symbols` lists each symbol the loader could not bind, with the
  object that needs it and, for a versioned symbol, the version it wants.
  This is the loader error, attributed to a library.
- `resolution_reason` on each node of the dependency graph says why that
  copy won: the library path, an `RPATH`/`RUNPATH`, or the default
  directories. A node resolved from a directory you did not expect is the
  first sign of a stale copy.
- `unresolved_libraries` lists needed SONAMEs found nowhere on the search
  path, for "cannot open shared object file".
- `bindings_summary` counts resolved and missing bindings; `loadability`
  is the overall pass or fail.

Also list every copy of the library the search could reach, not only the
winner. For example:

```bash
find "$(dirname path/to/program)/.." -name 'libname.so*'
```

When `abicheck` is unavailable, the loader can report the same facts
itself: `LD_DEBUG=libs,bindings path/to/program` shows the search and each
binding, `ldd path/to/program` shows the chosen copies, and `readelf -d`
shows `NEEDED`, `RPATH` and `RUNPATH`. `readelf -V` shows the version nodes
a library defines and a program requires.

`abicheck deps compare path/to/program --old-root OLD_SYSROOT --new-root NEW_SYSROOT`
compares the whole dependency stack between two environments, for "it
works in the old container and not in the new one".

## Compare built-against with loaded

The used copy may be older than the build's. To check, run the same
comparison with the two sides swapped (used as old, built-against as new):
only additions there means the used library is an older release, not one
that removed anything.

Old is the library the program was built against; new is the library the
loader actually used. Give each side its own headers:

```bash
abicheck compare BUILT_AGAINST.so LOADED.so \
  --header old=BUILT_AGAINST_HEADERS/lib.h --header new=LOADED_HEADERS/lib.h \
  -o json=compare.json
```

When the loaded side has no headers, compare with the built-against headers
on the old side only, and say the new side's layout came from debug info or
was not visible at all.

Findings to look for:

| Finding | Points to |
|---|---|
| `func_removed`, `var_removed` | `symbol_removed`; `stale_library_loaded` when a correct copy also exists; `library_older_than_build` when the reverse comparison shows only additions |
| only `func_added`, `var_added` (`COMPATIBLE`) | `not_an_abi_problem`: the program keeps working, and it can use the new symbols once rebuilt |
| `symbol_version_node_removed`, `symbol_moved_version_node` | `symbol_version_missing` |
| `type_size_changed`, `type_field_offset_changed`, `func_params_changed` | `layout_changed` |
| mangled names that differ only by `B5cxx11`, alongside `func_added_elf_only` | `cxx_abi_mismatch` |
| `NO_CHANGE`, and the loader resolved everything | `not_an_abi_problem` |

## Causes and fixes

The claim vocabulary calls the mechanism a cause; the names are the same.

| Cause | Fix, most preferred first |
|---|---|
| `symbol_removed` | restore the symbol in the library, or deprecate it with a compatibility alias; otherwise rebuild the program against the new library; otherwise pin the old library |
| `library_older_than_build` | deploy the library release the program was built against (or newer); otherwise build against the oldest release you must support, and state that minimum as a dependency constraint |
| `symbol_version_missing` | run on an environment whose library (or libstdc++/glibc) is at least as new as the build's; otherwise build against the oldest runtime you must support |
| `layout_changed` | rebuild the program against the headers of the library it will load; for the library, keep layouts stable (append-only fields, pImpl, reserved space) or bump the SONAME |
| `stale_library_loaded` | remove or reorder the stale copy on `LD_LIBRARY_PATH`/`RPATH`/`RUNPATH` so the intended copy wins; nothing needs rebuilding |
| `cxx_abi_mismatch` | build the program and the library with the same `_GLIBCXX_USE_CXX11_ABI` value (and the same standard library) |
| `not_an_abi_problem` | nothing to change in the libraries; if something fails, debug the program's own logic, inputs or configuration |

To verify, re-run the program the same way as in step 1. For a library-side
fix, also re-run the comparison and confirm the finding is gone.
