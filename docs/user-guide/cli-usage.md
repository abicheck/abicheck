# Using abicheck, Compatibility Modes, and Coverage

## What abicheck is

**abicheck** checks C/C++ library compatibility on both API and ABI layers.
It is designed to be a practical, modern replacement for legacy ABI tooling in CI,
especially when you need structured output and automation.

abicheck is inspired by:

- [libabigail / abidiff](https://sourceware.org/libabigail/)
- [ABI Compliance Checker (ABICC)](https://lvc.github.io/abi-compliance-checker/)

Huge thanks to both projects for pioneering ABI compatibility analysis.

> **Not sure which command fits your situation?** See
> [Choose Your Workflow](choose-your-workflow.md) — a decision guide that maps
> your artifacts (single library, release bundle, package, application, stripped
> binaries…) and CI policy to the exact command and options. This page is the
> per-command flag reference.

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
  --header old=v1/foo.h --header new=v2/foo.h --format sarif -o abi.sarif
abicheck compare libfoo.so.1 libfoo.so.2 \
  --header old=v1/foo.h --header new=v2/foo.h --format junit -o results.xml
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

If ELF headers are not provided, `compare` falls back to symbols-only analysis
and prints a warning. This mode is useful for quick checks but may miss
signature/type-level ABI breaks.

### 2) Dump snapshots and compare later (for CI baselines)

When you want to cache ABI baselines as CI artifacts or commit them to the repo:

```bash
# Step 1: Dump snapshots (each version uses its own header)
abicheck dump libfoo.so.1 -H include/v1/foo.h --version 1.0 -o libfoo-1.0.json
abicheck dump libfoo.so.2 -H include/v2/foo.h --version 2.0 -o libfoo-2.0.json

# Step 2: Compare snapshots (no headers needed — already baked in)
abicheck compare libfoo-1.0.json libfoo-2.0.json
```

If ELF headers are not provided, `compare` falls back to symbols-only analysis
and prints a warning. This mode is useful for quick checks but may miss
signature/type-level ABI breaks.

> **Going beyond a plain `.so` + headers?** C vs C++ mode, cross-compilation,
> feeding in the exact build flags (`-p build/`, evidence layer L3), embedding
> build/source evidence packs (L3/L4), resolving debug info that isn't in the
> binary itself, and `-v`/`--verbose` are all on their own reference page:
> [Evidence, Build-Context, and Debug Flags](dump-compare-flags.md).

### Related flags and pages

Beyond the core `compare`/`dump` flow:

- [Evidence, Build-Context, and Debug Flags](dump-compare-flags.md) — language
  mode, cross-compilation, `compile_commands.json` (L3), evidence packs
  (L3/L4), debug artifact resolution, `--dry-run`.
- [Output Formats](output-formats.md) — `--show-only` filtering, `--stat`,
  `--report-mode leaf`, `--show-impact`, redundancy filtering, SARIF/JUnit
  output, evidence-tier confidence, JSON schema.
- `--used-by`/`--required-symbol(s)` on `compare` scope the comparison to an
  application's actual imports or a plugin host's required entrypoints — see
  [Application Compatibility](appcompat.md) and [Plugin Systems](plugin-systems.md).
- Generating/validating/diffing Debian `dpkg-gensymbols`-style symbols files is
  a Python API only now (`abicheck.debian_symbols`), not a CLI subcommand — see
  [Debian Symbols File Integration](debian-symbols.md).

The `--profile` shortcut and severity configuration below are core to every
`compare` invocation, so they stay on this page.

#### Severity configuration (`--severity-*`)

Control exit codes and report labels by assigning severity levels to four issue
categories:

```bash
# Block on API additions
abicheck compare old.json new.json --severity-addition error

# Everything is an error (strict)
abicheck compare old.json new.json --severity-preset strict

# Custom: breaks are errors, additions are warnings, rest is info
abicheck compare old.json new.json \
  --severity-abi-breaking error \
  --severity-potential-breaking info \
  --severity-quality-issues info \
  --severity-addition warning
```

See the [severity guide](severity.md) for the full reference.

#### `--profile`: one token for a whole workflow

Common invocations bundle the same handful of flags. `--profile NAME` expands
to a named set of workflow defaults so you don't retype them (ADR-040). An
explicit flag always overrides the profile, so a profile is a starting point,
not a straitjacket.

| Profile | Expands to | Use when |
|---------|-----------|----------|
| `ci-gate` | `--depth headers --format review --exit-code-scheme severity` | Blocking a PR in CI |
| `release` | `--depth source --format markdown --recommend` | Deciding a version bump at release time |
| `quick` | `--depth binary --stat` | A fast "just tell me" look |

Precedence is **explicit flag > profile > project config > default**: a
`--profile` is a per-run choice you typed, so it overrides `.abicheck.yml`
defaults, while any flag you type still overrides the profile. Public-surface
scoping is on by default, so the profiles don't restate it.

Profiles are **single-pair-only** — they bundle single-pair knobs (`--depth`,
`--exit-code-scheme`, the `review` digest) that the directory/package *release
fan-out* doesn't accept. Passing `--profile` with two directories/packages is a
usage error; configure release defaults (format, severity, scheme) in
`.abicheck.yml`, which the fan-out reads.

```bash
# CI gate — equivalent to the three flags in the table
abicheck compare old.json new.json --profile ci-gate

# Start from the release profile but force JSON output (explicit flag wins)
abicheck compare old.json new.json --profile release --format json
```

> `--show-only` filtering, `--show-redundant`, `--stat`, `--report-mode leaf`,
> and `--show-impact` are covered in full on
> [Output Formats](output-formats.md) — all are display-only and do not affect
> the verdict or exit code.

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

For teams migrating from `abi-compliance-checker` — same flags, same XML descriptors.
See the [Migrating from ABICC](from-abicc.md) guide and the
[ABICC Flag Reference](../reference/abicc-flags.md) for the full flag list.

```bash
# Minimal (identical to abi-compliance-checker):
abicheck compat check -lib foo -old old.xml -new new.xml

# With strict mode and version labels:
abicheck compat check -lib foo -old old.xml -new new.xml -s -v1 1.0 -v2 2.0

# Source/API compat only (ignore ELF metadata):
abicheck compat check -lib foo -old old.xml -new new.xml -source

# Skip known symbols:
abicheck compat check -lib foo -old old.xml -new new.xml -skip-symbols skip.txt
```

## abicheck as a drop-in replacement for ABICC

abicheck intentionally supports ABICC-like CLI semantics and XML descriptor flow,
while modernizing internals and outputs.

### Why teams replace ABICC with abicheck

- Python-native implementation, easier to embed and extend in CI.
- Structured outputs (`json`, `markdown`, `sarif`) for machine + human consumption.
- Works well in stripped-binary workflows when combined with headers.
- Better integration path for modern C++ workflows and policy checks.
- **Full ABICC flag parity** — `-s/-strict`, `-source`, `-skip-symbols/-skip-types`, `-v1/-v2`, `-stdout` and more.
- **Superset detectors** — catches everything ABICC catches plus: `FUNC_DELETED`, `VAR_BECAME_CONST`, `TYPE_BECAME_OPAQUE`, `BASE_CLASS_POSITION_CHANGED`, `BASE_CLASS_VIRTUAL_CHANGED`.

### Practical migration path

1. Keep your existing ABICC XML descriptor generation.
2. Replace ABICC compare call with `abicheck compat check ...` (flags are identical).
3. Optionally move to native `dump/compare` commands for explicit snapshot control.
4. Switch CI gates to JSON/SARIF-based policy checks.

## Change classification: BREAKING vs COMPATIBLE

abicheck classifies every detected change into a verdict:

- **BREAKING** — binary ABI incompatibility; existing binaries will malfunction.
- **COMPATIBLE** — informational/warning; does not break binary compatibility on its own.
- **NO_CHANGE** — identical ABI.

A change is BREAKING only when it causes binary-level failures: symbol resolution errors,
type layout corruption, vtable mismatch, or calling convention incompatibility.

Changes like enum member addition, union field addition (without growth),
GLOBAL→WEAK binding, and IFUNC transitions are classified as **COMPATIBLE** — they are
detected and reported for awareness but do not trigger a BREAKING verdict.
`noexcept` removal is classified **COMPATIBLE_WITH_RISK** (binary-linkable but a
deployment/behavioral hazard). See
[ABI/API Handling & Recommendations](../concepts/abi-api-handling.md) for the full
rationale.

## ABI/API breakages and what each tool mode can detect

The per-case matrix comparing abicheck, `abidiff`, and ABICC modes across the
catalog lives in the **[Tool Comparison & Benchmarks](../reference/tool-comparison.md)**
reference, and every case has a full reproduction in the
[Examples Encyclopedia](../examples/index.md). The qualitative takeaway:

- **API surface breaks** (removed/changed signatures): all modes generally catch these.
- **C++ semantic contract breaks** (`noexcept`, inline/ODR): header-aware analysis is strongest.
- **DWARF-only detail** (some anonymous/internal layout details): ABICC dump mode can be strongest when debug info exists.
- **Policy/linking hygiene** (SONAME/versioning/visibility): best handled by a tool that includes explicit ELF policy checks.

## Architecture and dependencies

### 5) Full-stack dependency validation (Linux ELF)

Resolve the full dependency tree, simulate symbol binding, and produce a
stack-level ABI compatibility verdict.

```bash
# Show dependency tree + symbol binding status
abicheck deps tree /usr/bin/python3
abicheck deps tree /usr/bin/python3 --format json

# Compare a binary's full stack across two sysroots
abicheck deps compare usr/bin/myapp \
    --old-root /rootfs/v1 --new-root /rootfs/v2

# Include dependency info in dump/compare
abicheck dump libfoo.so -H foo.h --follow-deps -o snap.json
abicheck compare old.so new.so -H foo.h --follow-deps
```

The `deps tree` command resolves the transitive dependency closure and displays:
- Dependency tree with resolution reasons (rpath, runpath, default, etc.)
- Unresolved libraries
- Symbol binding summary (resolved, missing, version mismatches)

The `deps compare` command compares two environments and reports:
- Loadability verdict (will the binary load?)
- ABI risk verdict (are there breaking changes in dependencies?)
- Per-library ABI diffs intersected with actual symbol usage

The `--follow-deps` flag on `dump` and `compare` includes dependency graph
and binding information in the output alongside the regular ABI diff.

Packaging-specific integration — generating/validating/diffing Debian
`dpkg-gensymbols`-style symbols files — is on its own page:
[Debian Symbols File Integration](debian-symbols.md).

## Architecture and runtime dependencies

For the internal pipeline and module map (dumper → checker → resolver → reporters),
see the [Codebase Overview](../development/codebase-overview.md) and the
[Architecture](../concepts/architecture.md) concept page. For the runtime
dependencies (Python 3.10+, castxml, pyelftools, …) and per-platform setup, see
[Getting Started](../getting-started.md#requirements).

