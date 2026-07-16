# Debian Symbols File Integration

Generate, validate, and diff [Debian symbols files](https://manpages.debian.org/unstable/dpkg-dev/dpkg-gensymbols.1.en.html) for integration with Debian/Ubuntu packaging workflows where `dpkg-gensymbols` and `dpkg-shlibdeps` use symbols files for fine-grained dependency tracking.

> **No CLI command anymore.** The pre-1.0 CLI reset (ADR-043) removed the
> standalone `abicheck debian-symbols` command (`generate`/`validate`/`diff`
> subcommands) with **no CLI replacement**. The underlying logic still exists
> as plain Python functions in `abicheck/debian_symbols.py` — everything
> below is documented as a Python API, not a shell command. If you have a
> packaging script that shelled out to `abicheck debian-symbols ...`, replace
> it with a small Python script calling these functions directly (see
> examples below), or a project-local wrapper CLI of your own.

> Split out of [CLI Usage](cli-usage.md), which covers the core `dump`/
> `compare` flow.

## Generate a symbols file from a shared library

```python
from pathlib import Path
from abicheck.debian_symbols import generate_from_binary

symbols_file = generate_from_binary(
    Path("libfoo.so"), package="libfoo1", version="1.0",
)
Path("debian/libfoo1.symbols").write_text(symbols_file.format())
```

Use `use_cpp=False` to emit mangled names only (no demangling):

```python
symbols_file = generate_from_binary(Path("libfoo.so"), use_cpp=False)
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
- **Package name** is derived from SONAME (e.g. `libfoo.so.1` → `libfoo1`) or set with `package=`

## Validate a symbols file against a binary

```python
from pathlib import Path
from abicheck.debian_symbols import validate_from_binary, format_validation_report

result = validate_from_binary(Path("libfoo.so"), Path("debian/libfoo1.symbols"))
print(format_validation_report(result))
print("passed" if result.passed else "failed")
```

Example output (`format_validation_report`):

```
Symbols validation for libfoo.so.1:
  MISSING from binary (in symbols file but not exported):
    _ZN3foo6legacyEv@Base 1.0
  NEW in binary (exported but not in symbols file):
    _ZN3foo9new_thingEv@Base
  Result: FAIL (1 missing symbol)
```

Symbols tagged `(optional)` are not required — their absence does not cause failure, matching `dpkg-gensymbols` semantics.

## Diff two symbols files

```python
from abicheck.debian_symbols import load_symbols_file, diff_symbols_files, format_diff_report

old = load_symbols_file("old/libfoo1.symbols")
new = load_symbols_file("new/libfoo1.symbols")
diff = diff_symbols_files(old, new)
print(format_diff_report(diff, "old/libfoo1.symbols", "new/libfoo1.symbols"))
```

Example output (`format_diff_report`):

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

## API reference

| Function | Description |
|----------|--------------|
| `generate_symbols_file(elf_meta, *, package="", version="#MINVER#", use_cpp=True)` | Build a `DebianSymbolsFile` from already-parsed `ElfMetadata` |
| `generate_from_binary(so_path, *, package="", version="#MINVER#", use_cpp=True)` | Convenience wrapper: parse a `.so` and generate its symbols file in one call |
| `parse_symbols_file(text)` / `load_symbols_file(path)` | Parse an existing Debian symbols file (from a string or from disk) |
| `validate_symbols(elf_meta, symbols_file)` / `validate_from_binary(so_path, symbols_path)` | Compare a symbols file against a binary's actual exports; returns a `ValidationResult` |
| `diff_symbols_files(old, new)` | Diff two `DebianSymbolsFile` objects; returns a `SymbolsDiff` |
| `format_validation_report(result)` / `format_diff_report(diff, old_path, new_path)` | Render a `ValidationResult`/`SymbolsDiff` as the human-readable text shown above |

`DebianSymbolsFile.format()` renders the file back to Debian symbols-file text
(the inverse of `parse_symbols_file`/`load_symbols_file`).

## Supported tag syntax

The parser handles the full Debian symbols tag syntax:

- `(c++)` — C++ demangled symbol with quoted name
- `(optional)` — symbol is not required during validation
- `(arch=amd64)` — architecture-specific symbol (parsed, not filtered yet)
- `(c++|optional)` — pipe-separated tag groups (round-trip preserved)
- `(symver)`, `(regex)` — parsed but not evaluated

## Limitations

- No CLI command — Python API only (see above).
- `#include` directives and `#PACKAGE#` substitution are not supported.
- `(regex)` and `(symver)` pattern-matching tags are parsed but not evaluated.
- `(arch=...)` tags are parsed but not filtered (no arch-filtering option yet).
