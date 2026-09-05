# Case 50: SONAME Inconsistent (Wrong Major Version)

**Category:** Risk | **Verdict:** ⚠️ COMPATIBLE_WITH_RISK (bad practice)

## Verdict and consumer impact

Both libraries export the same symbols with identical signatures — the
functional ABI is unchanged. What differs is `DT_SONAME` metadata: v1 uses
`libfoo.so.0` while the project's actual release is 1.x, so the correct
SONAME should be `libfoo.so.1`. This is a packaging/upgrade hazard rather
than a binary compatibility break: every consumer linked against v1 bakes
`libfoo.so.0` into its own `DT_NEEDED`, so fixing the SONAME to `libfoo.so.1`
later forces those consumers to relink even though nothing about the actual
API changed.

## Old/new diff

| bad.c (v1, linked `-Wl,-soname,libfoo.so.0`) | good.c (v2, linked `-Wl,-soname,libfoo.so.1`) |
|-----------------------------------------------|-------------------------------------------------|
| `int foo(void) { return 42; }` | `int foo(void) { return 42; }` |
| `int bar(int x) { return x + 1; }` | `int bar(int x) { return x + 1; }` |

## abicheck command

```bash
gcc -shared -fPIC -g bad.c -o libfoo_v1.so -Wl,-soname,libfoo.so.0
gcc -shared -fPIC -g good.c -o libfoo_v2.so -Wl,-soname,libfoo.so.1
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE_WITH_RISK (exit 0)

Deployment Risk Changes:
- soname_changed: SONAME changed: 'libfoo.so.0' -> 'libfoo.so.1'

Quality Issues:
- soname_bump_unnecessary: SONAME changed from 'libfoo.so.0' to
  'libfoo.so.1' but no binary-incompatible changes were detected. This
  forces all consumers to relink unnecessarily. Consider whether the bump
  was intentional.
```

## Minimum evidence

`min_evidence: L0` — `DT_SONAME` is an ELF `.dynamic`-section entry read
directly from each binary; no debug info or headers are needed to compare
it.

## Why abicheck catches it

abicheck reads the `DT_SONAME` entry from each library's `.dynamic` section
and compares the two strings directly. Because the exported symbol table and
types are otherwise identical, it also cross-checks whether the SONAME bump
was actually warranted by a real break — finding none, it flags the bump
itself (`soname_bump_unnecessary`) as a separate quality issue on top of the
factual `soname_changed` risk note.

## Runtime failure demonstration

**Severity: LOW (bad practice) — no failure while the wrong SONAME's file
is still present; failure appears one step later, on cleanup/upgrade.**

```bash
# Build v1 with its (wrong) SONAME and an app linked against it
gcc -shared -fPIC -g bad.c -o libfoo.so.0 -Wl,-soname,libfoo.so.0
gcc -g app.c -L. -l:libfoo.so.0 -Wl,-rpath,. -o app
readelf -d app | grep NEEDED
# → (NEEDED) Shared library: [libfoo.so.0]   ← wrong major baked in

./app
# → foo() = 42
# → bar(5) = 6

# Now imagine the "proper" upgrade: only the correctly-SONAMEd v2 library
# is installed, and the old libfoo.so.0 file is removed.
rm libfoo.so.0
# (v2, correctly SONAMEd, is present as libfoo.so.1)
./app
# → ./app: error while loading shared libraries: libfoo.so.0: cannot open
#   shared object file: No such file or directory
```

**Why bad practice, not a hard break today:** the app runs fine as long as
some file named `libfoo.so.0` is present — v1's wrong SONAME baked itself
into the app's own `DT_NEEDED` at link time. The failure only appears later,
when the package is "corrected" to ship the properly-versioned
`libfoo.so.1` and the stale `libfoo.so.0` is no longer provided — exactly
the upgrade-path breakage SONAME conventions exist to prevent.

## Safe redesign

Set SONAME to match your project's actual ABI major version from the start:

```bash
gcc -shared -fPIC lib.c -o libfoo.so.1.2.3 -Wl,-soname,libfoo.so.1
ln -sf libfoo.so.1.2.3 libfoo.so.1
ln -sf libfoo.so.1 libfoo.so
```

In CMake:

```cmake
set_target_properties(foo PROPERTIES
    VERSION 1.2.3
    SOVERSION 1
)
```

**Real-world example:** Debian's shared library policy requires SONAME to
follow the `libname.so.MAJOR` convention; packages with inconsistent
SONAMEs are rejected during review because they break upgrade paths.

## Cross-tool comparison

```bash
readelf -d libfoo_v1.so | grep SONAME
readelf -d libfoo_v2.so | grep SONAME
```

## References

- [Debian Library Packaging Guide](https://www.debian.org/doc/debian-policy/ch-sharedlibs.html)
- [How To Write Shared Libraries — SONAME](https://www.akkadia.org/drepper/dsohowto.pdf)
