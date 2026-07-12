# Debian Symbols File Integration

Generate, validate, and diff [Debian symbols files](https://manpages.debian.org/unstable/dpkg-dev/dpkg-gensymbols.1.en.html) for integration with Debian/Ubuntu packaging workflows where `dpkg-gensymbols` and `dpkg-shlibdeps` use symbols files for fine-grained dependency tracking.

> Split out of [CLI Usage](cli-usage.md), which covers the core `dump`/
> `compare` flow.

## Generate a symbols file from a shared library

```bash
# Generate and print to stdout
abicheck debian-symbols generate libfoo.so

# Write to file with explicit package name and version
abicheck debian-symbols generate libfoo.so -o debian/libfoo1.symbols \
    --package libfoo1 --version 1.0
```

Output format (Debian symbols file):

```
libfoo.so.1 libfoo1 1.0
 _ZN3foo3barEv@Base 1.0
 _ZN3foo3bazEi@Base 1.0
 _ZN3foo9new_thingEv@LIBFOO_2.0 1.1
 (c++)"foo::Config::Config()@Base" 1.0
 (c++)"foo::Config::~Config()@Base" 1.0
```

Mapping:

- **Library SONAME** → first line (library, package name, minimum version)
- **Each exported symbol** → one line with `@Base` or `@VERSION_NODE` and version
- **C++ symbols** → demangled form with `(c++)` prefix (Debian convention)
- **Version** comes from ELF symbol version nodes if present, else `@Base`
- **Package name** is derived from SONAME (e.g. `libfoo.so.1` → `libfoo1`) or set with `--package`

Use `--no-cpp` to emit mangled names only (no demangling):

```bash
abicheck debian-symbols generate libfoo.so --no-cpp -o symbols
```

## Validate a symbols file against a binary

```bash
abicheck debian-symbols validate libfoo.so debian/libfoo1.symbols
```

Example output:

```
Symbols validation for libfoo.so.1:
  MISSING from binary (in symbols file but not exported):
    _ZN3foo6legacyEv@Base 1.0
  NEW in binary (exported but not in symbols file):
    _ZN3foo9new_thingEv@Base
  Result: FAIL (1 missing symbol)
```

Exit codes: `0` = match (symbols file is valid), `2` = mismatch (missing symbols).

Symbols tagged `(optional)` are not required — their absence does not cause failure, matching `dpkg-gensymbols` semantics.

## Diff two symbols files

```bash
abicheck debian-symbols diff old/libfoo1.symbols new/libfoo1.symbols
```

Example output:

```
Symbols diff: old/libfoo1.symbols -> new/libfoo1.symbols
  ADDED:
    + _ZN3foo9new_thingEv@Base 1.1
  REMOVED:
    - _ZN3foo6legacyEv@Base 1.0
  VERSION CHANGED:
    (none)
  Total changes: 2
```

## Options reference

| Subcommand | Option | Description |
|------------|--------|-------------|
| `generate` | `-o` / `--output` | Output file path (stdout if omitted) |
| `generate` | `--package` | Debian package name (derived from SONAME if empty) |
| `generate` | `--version` | Minimum version string (default: `#MINVER#`) |
| `generate` | `--no-cpp` | Emit mangled names only, no `(c++)` demangled form |
| `validate` | _(positional)_ | `SO_PATH SYMBOLS_PATH` — binary and symbols file |
| `diff` | _(positional)_ | `OLD_SYMBOLS NEW_SYMBOLS` — two symbols files |

## Supported tag syntax

The parser handles the full Debian symbols tag syntax:

- `(c++)` — C++ demangled symbol with quoted name
- `(optional)` — symbol is not required during validation
- `(arch=amd64)` — architecture-specific symbol (parsed, not filtered yet)
- `(c++|optional)` — pipe-separated tag groups (round-trip preserved)
- `(symver)`, `(regex)` — parsed but not evaluated

## Limitations

- `#include` directives and `#PACKAGE#` substitution are not supported.
- `(regex)` and `(symver)` pattern-matching tags are parsed but not evaluated.
- `(arch=...)` tags are parsed but not filtered (no `--arch` option yet).
