# SONAME, semantic versioning, and how they relate

Two dials that people conflate. The version number is a **promise to
humans**; the SONAME (or its platform equivalent) is a **binding used by the
dynamic loader**. Getting one right does not get the other right.

## The loader-facing identity

| Platform | Identity | Effect of changing it |
|---|---|---|
| ELF (Linux, BSD) | `SONAME` — conventionally `libfoo.so.MAJOR` | old consumers keep resolving the old file; both can coexist |
| Mach-O (macOS) | install name (the ABI epoch) + compatibility/current version | a new install name lets both coexist; see the note below on which direction `compatibility_version` actually guards |
| PE (Windows) | the DLL file name itself | old consumers keep loading the old DLL name |

The rule: **an incompatible change must change the loader-facing identity.**
Otherwise a consumer that linked against the old library silently picks up
the new, incompatible one and fails at load time or, worse, at runtime.

**Mach-O's `compatibility_version` does not do this, and assuming it does is
a real way to ship a break.** dyld records, in each client, the compatibility
version of the dylib it linked against, and at load time refuses a dylib
whose compatibility version is **lower** than that recorded number. So the
check guards against *downgrades*; raising the compatibility version does
**not** turn away older clients — they recorded a lower number and load
happily against the new, possibly incompatible, dylib. On macOS the ABI epoch
is therefore the **install name**: change it (`libfoo.2.dylib`,
`@rpath/libfoo.2.dylib`) for an incompatible generation, and reserve
compatibility-version bumps for backward-compatible evolution.

Bumping the package version without bumping the SONAME does not protect
anyone. Bumping the SONAME without a version bump confuses humans but is at
least safe.

## Version-number schemes

**Semantic versioning** applied to a native library, in ABI terms:

- **MAJOR** — any removal or incompatible change to the public ABI or API.
  Accompanied by a SONAME bump **when the break is binary** (`BREAKING`).
  A source-only break (`API_BREAK` — e.g. a removed overload never emitted)
  is still a major version by this scheme, but already-compiled consumers
  keep working, so the SONAME usually stays; see the source-only row below.
- **MINOR** — additive only: new exports, new types, and an appended
  enumerator *whose underlying type is fixed or whose value fits the old
  representation* (an append that widens an unfixed underlying type is a
  binary break — see the table below). Existing consumers keep working
  unmodified.
- **PATCH** — no public surface change at all; implementation only.

Pre-1.0 makes no compatibility promise between minors — say so explicitly
rather than applying the post-1.0 rule to a `0.x` project.

**libtool current:revision:age** encodes the same information differently
(`age` is how many prior interface versions are still supported). If the
project uses it, map to it rather than recommending a semver number it does
not use.

**Distro / package epoch and release fields** are orthogonal to both and
never encode ABI.

## Which change forces which bump

| Change | Version | SONAME |
|---|---|---|
| implementation-only | patch | unchanged |
| new export / new type | minor | unchanged |
| appended enumerator, underlying type fixed or value in range | minor | unchanged |
| appended enumerator that widens an unfixed underlying type | major | bump |
| new virtual on a class consumers derive from | major | bump |
| changed layout of a public aggregate | major | bump |
| removed or renamed export | major | bump |
| changed signature (including a defaulted parameter) | major | bump |
| source-only break (removed overload never emitted, tightened template) | project's choice | usually unchanged |
| raised symbol-version floor (`GLIBC_*`, `GLIBCXX_*`, `CXXABI_*`) | not an ABI change in itself — document the new floor; treat as a risk, or a break where a declared floor is exceeded | unchanged |

The source-only row is the one that genuinely varies by project. Resolve it
against the project's stated scheme and say which rule was applied.

## Multi-library releases

A release shipping several libraries has a **per-library** ABI answer and a
**release-level** decision. Two things a per-library comparison cannot see:

- A library **removed** from the shipped set — use
  `compare --fail-on-removed-library` on the directory pair.
- **Inter-library** compatibility, where one shipped library is itself a
  consumer of another. Scope it with `--used-by`:
  [consumer scoping](../../shared/consumer-scoping.md).

Whether the whole release shares one version number or each library carries
its own is a project convention; state which one you assumed.

## Runtime floors are not ABI — but `compare` still reports them

A perfectly ABI-compatible library still fails on an older system if the new
build raised its glibc or libstdc++ requirement. That is a different contract
from ABI, and it does not by itself force a SONAME bump.

It is **not** invisible to `compare`, though. A raised `GLIBC_*`/`GLIBCXX_*`/
`CXXABI_*` symbol version is emitted as `runtime_floor_raised` — a risk on
its own, promoted to a break where it exceeds a declared floor. Read it from
the findings; do not defer it and then omit it.

`abicheck deps compare` and `abicheck deps tree` cover what `compare` does
not: the wider dependency graph. Report the floor as a separate,
explicitly-scoped statement rather than folding it into the ABI verdict
([compatibility contracts](../../shared/compatibility-contracts.md)).
