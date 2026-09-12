---
doc_type: how-to
audience:
  - library-maintainer
level: beginner
canonical_for:
  - cli-surface
lifecycle: active
generated: false
---

# Core CLI Workflows

## What abicheck is

**abicheck** checks C/C++ library compatibility on both API and ABI layers.
It is designed to be a practical, modern replacement for legacy ABI tooling in CI,
especially when you need structured output and automation.

abicheck is inspired by:

- [libabigail / abidiff](https://sourceware.org/libabigail/)
- [ABI Compliance Checker (ABICC)](https://lvc.github.io/abi-compliance-checker/)

Huge thanks to both projects for pioneering ABI compatibility analysis.

> **Not sure which command fits your situation?** See
> [Choose Your Workflow](../start/choose-your-workflow.md) — a decision guide that maps
> your artifacts (single library, release bundle, package, application, stripped
> binaries…) and CI policy to the exact command and options. This page covers
> the core `dump`/`compare` workflow; for the exhaustive, generated
> per-command flag list see the [CLI Reference](../reference/cli-reference.md).

## How to use abicheck

### 1) Compare two libraries directly (primary flow)

The simplest way — pass `.so` files and their public headers directly to
`compare`. Each library version gets its own header(s):

```bash
# Each version has its own header
abicheck compare libfoo.so.1 libfoo.so.2 \
  --header old=include/v1/foo.h --header new=include/v2/foo.h

# Multiple headers per version, with include dirs and version labels
abicheck compare libfoo.so.1 libfoo.so.2 \
  --header old=include/v1/foo.h --header old=include/v1/bar.h \
  --header new=include/v2/foo.h --header new=include/v2/bar.h \
  -I include/ --version old=1.0 --version new=2.0

# Shorthand: -H applies the same header to both sides
# (only when the header itself didn't change between versions)
abicheck compare libfoo.so.1 libfoo.so.2 -H include/foo.h

# Header directory input is supported (recursive)
abicheck compare libfoo.so.1 libfoo.so.2 -H include/

# Output formats
abicheck compare libfoo.so.1 libfoo.so.2 \
  --header old=v1/foo.h --header new=v2/foo.h -o sarif=abi.sarif
abicheck compare libfoo.so.1 libfoo.so.2 \
  --header old=v1/foo.h --header new=v2/foo.h -o junit=results.xml
```

#### Public headers vs. include roots

`-H/--header` and `-I/--include` look similar but answer different questions:

| Flag | Question | Role |
|------|----------|------|
| `-H` / `--header` (`--header old=`/`--header new=`) | **What** to analyse | The **public headers** — the files a consumer `#include`s. These *are* the API surface abicheck parses to decide what's public and to read types. Pass a directory to establish a public/internal boundary. |
| `-I` / `--include` (`--include old=`/`--include new=`) | **How** to parse it | The **include roots** — directories added to the parser's search path so the public headers' *own* `#include "…"`/`<…>` lines resolve. They are **not** analysed; they only make the parse succeed. |

Often a single `include/` is both the public header dir and the include root. But
they diverge when a public header pulls in a dependency from elsewhere — e.g.
`include/foo/api.h` doing `#include <bar/baz.h>` needs `third_party/` added as an
include root (`-I third_party`) even though `bar/baz.h` itself is not part of
`foo`'s public API. If the parser can't find an included file, add its directory
as an include root.

`compare` auto-detects each input: `.so` files are dumped on-the-fly, `.json`
snapshots and ABICC Perl dumps (Data::Dumper `.dump` files) are loaded directly.
You can mix them freely (see below).

If headers are not provided, `compare` uses whatever debug info is available
instead — DWARF (falling back to BTF/CTF) on ELF, PDB on PE — falling back further to L0
binary-metadata analysis (exported symbols plus platform-specific facts —
SONAME/dependencies/rpaths on ELF, machine type/imports/delay-load/hardening
on PE, install name/dependencies/rpaths on Mach-O — never a bare symbol
list) only when neither headers nor debug info are present. Only the ELF
path prints an explicit no-headers warning today; PE and Mach-O degrade the
same way without one. Less evidence means a weaker analysis that may miss
signature/type-level ABI breaks; see
[Evidence & Detectability](../learn/evidence-and-detectability.md).

### 2) Dump snapshots and compare later (for CI baselines)

When you want to cache ABI baselines as CI artifacts or commit them to the repo:

```bash
# Step 1: Dump snapshots (each version uses its own header)
abicheck dump libfoo.so.1 -H include/v1/foo.h --version 1.0 -o libfoo-1.0.json
abicheck dump libfoo.so.2 -H include/v2/foo.h --version 2.0 -o libfoo-2.0.json

# Step 2: Compare snapshots (no headers needed — already baked in)
abicheck compare libfoo-1.0.json libfoo-2.0.json
```

A JSON snapshot's evidence is fixed at `dump` time: `compare` loads it
verbatim, so passing `-H` at `dump` time (as above) means `compare` sees
the same full header-AST evidence either side captured, with no headers
flag of its own to pass and no re-resolution against DWARF/PDB/binary
metadata happening at compare time. The no-headers fallback described
above only applies when a *native binary* (`.so`/`.dll`/`.dylib`) is
compared directly with no `-H`; see
[Evidence & Detectability](../learn/evidence-and-detectability.md).

> **Going beyond a plain `.so` + headers?** C vs C++ mode, cross-compilation,
> feeding in the exact build flags (`-p build/`, evidence layer L3), embedding
> build/source evidence packs (L3/L4), resolving debug info that isn't in the
> binary itself, and `-v`/`--verbose` are all on their own reference page:
> [Evidence, Build-Context, and Debug Flags](dump-compare-flags.md).

### Related flags and pages

For the exhaustive, generated list of every command/subcommand/option (the
same `help=` text `--help-all` shows), see the [CLI Reference](../reference/cli-reference.md).

`compare`, `dump`, and `scan` each show only a curated, everyday subset by
default (`-H`, `--depth`, `--output`, and the like); the long tail —
toolchain overrides, debug-info resolution, per-category severity, release-
only knobs — folds behind `--help-all` on each command. Nothing is removed,
only hidden from the default view; every folded option still works exactly
as documented when passed explicitly.

Beyond the core `compare`/`dump` flow:

- [Evidence, Build-Context, and Debug Flags](dump-compare-flags.md) — language
  mode, cross-compilation, `compile_commands.json` (L3), evidence packs
  (L3/L4), debug artifact resolution, `--dry-run`.
- [Output Formats](output-formats.md) — `--view show=...` filtering,
  `-o oneline=...`'s one-line summary, `--view leaf|impact`,
  redundancy filtering, SARIF/JUnit output, evidence-tier confidence, JSON
  schema.
- `--used-by`/`--required-symbol(s)` on `compare` scope the comparison to an
  application's actual imports or a plugin host's required entrypoints — see
  [Application Compatibility](appcompat.md) and [Plugin Systems](plugin-systems.md).
- Generating/validating/diffing Debian `dpkg-gensymbols`-style symbols files is
  a Python API only now (`abicheck.debian_symbols`), not a CLI subcommand — see
  [Debian Symbols File Integration](debian-symbols.md).

`--severity-*` (controlling exit codes and report labels) is covered in full
on [Severity Configuration](severity.md).

#### There is no `--profile` shortcut

An earlier revision offered `--profile NAME` (`ci-gate`/`release-cut`/`quick`)
to bundle a handful of flags into one token (ADR-040). It was removed
(ADR-068 D5 / plan Phase 7e): it bundled three independent axes — evidence
depth, report rendering, and CI gate policy — behind one word, and a
rendering choice may never carry a gate setting. State the three
independently instead:

```bash
# What --profile ci-gate used to expand to
abicheck compare old.json new.json --depth headers -o review=- \
  --severity-preset default

# What --profile release-cut used to expand to
abicheck compare old.json new.json --depth source -o markdown=-

# What --profile quick used to expand to (-o oneline=... replaces the
# one-line summary; there is no depth-bundling replacement)
abicheck compare old.json new.json -o oneline=-
```

A project that wants `ci-gate`'s behavior on every run states depth, view,
and severity in `.abicheck.yml` instead of retyping a preset.

> `--view show=...` filtering, `scope.show_redundant: true`, `-o oneline=...`'s
> one-line summary format, and `--view leaf|impact` are covered in full
> on [Output Formats](output-formats.md). `--view show=...`/`show_redundant`/
> `--view leaf|impact|root-cause` are display-only and do not affect the verdict or exit
> code.

### 3) Mixed mode: snapshot baseline vs live build

```bash
# CI baseline snapshot vs current build
abicheck compare baseline-1.0.json ./build/libfoo.so \
  --header new=include/foo.h --version new=2.0-dev

# Live old build vs stored new snapshot
abicheck compare ./build-old/libfoo.so new-release.json \
  --header old=include/foo.h --version old=1.0-rc1
```

### 4) ABICC-compatible invocation (for migration)

For teams migrating from `abi-compliance-checker` — same flags, same XML
descriptors — `abicheck compat check`/`compat dump` are a drop-in
replacement. See [Migrating from ABICC](from-abicc.md) for the full flag
table, behavior differences (`-strict` semantics, XML descriptor format),
and worked examples, and the
[ABICC Flag Reference](../reference/abicc-flags.md) for the exhaustive flag
list.

## Change classification and detection coverage

What each verdict (`BREAKING`/`API_BREAK`/`COMPATIBLE`/`COMPATIBLE_WITH_RISK`/`NO_CHANGE`)
means is covered in full on [Verdicts](../learn/verdicts.md); the per-case
matrix comparing abicheck, `abidiff`, and ABICC detection coverage across the
example catalog is the
[Tool Comparison & Benchmarks](../reference/tool-comparison.md) reference.

## Dependency-stack commands

`deps tree`/`deps compare` (full dependency-closure resolution and
cross-sysroot ABI diffing, Linux ELF) and the `--follow-deps` flag are
covered with worked examples and exit codes on
[Migrating to the Current CLI](companion-commands.md#deps-tree). Packaging
integration — generating/validating/diffing Debian `dpkg-gensymbols`-style
symbols files — is its own page:
[Debian Symbols File Integration](debian-symbols.md).

## Architecture and runtime dependencies

For the internal pipeline and module map (dumper → checker → resolver → reporters),
see the [Codebase Overview](../contribute/codebase-overview.md) and the
[Architecture](../learn/architecture.md) concept page. For the runtime
dependencies (Python 3.10+, castxml, pyelftools, …) and per-platform setup, see
[Install abicheck](../start/install.md#requirements).

