# SONAME, semantic versioning, and how they relate

Two dials that people conflate. The version number is a **promise to
humans**; the SONAME (or its platform equivalent) is a **binding used by the
dynamic loader**. Getting one right does not get the other right.

## The loader-facing identity

| Platform | Identity | Effect of changing it |
|---|---|---|
| ELF (Linux, BSD) | `SONAME` — conventionally `libfoo.so.MAJOR` | old consumers keep resolving the old file; both can coexist |
| Mach-O (macOS) | install name + compatibility/current version | a raised compatibility version refuses old consumers at load |
| PE (Windows) | the DLL file name itself | old consumers keep loading the old DLL name |

The rule: **an incompatible change must change the loader-facing identity.**
Otherwise a consumer that linked against the old library silently picks up
the new, incompatible one and fails at load time or, worse, at runtime.

Bumping the package version without bumping the SONAME does not protect
anyone. Bumping the SONAME without a version bump confuses humans but is at
least safe.

## Version-number schemes

**Semantic versioning** applied to a native library, in ABI terms:

- **MAJOR** — any removal or incompatible change to the public ABI or API.
  Accompanied by a SONAME bump.
- **MINOR** — additive only: new exports, new types, new enumerators
  appended. Existing consumers keep working unmodified.
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
| new export / new type / appended enumerator | minor | unchanged |
| new virtual on a class consumers derive from | major | bump |
| changed layout of a public aggregate | major | bump |
| removed or renamed export | major | bump |
| changed signature (including a defaulted parameter) | major | bump |
| source-only break (removed overload never emitted, tightened template) | project's choice | usually unchanged |
| raised dependency floor (glibc, libstdc++) | not an ABI change — document it | unchanged |

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

## Runtime floors are not ABI

A perfectly ABI-compatible library still fails on an older system if the new
build raised its glibc or libstdc++ requirement. `compare` does not judge
this. Use `abicheck deps compare` and `abicheck deps tree`, and report the
floor as a separate, explicitly-scoped statement — never as part of the ABI
verdict ([compatibility contracts](../../shared/compatibility-contracts.md)).
