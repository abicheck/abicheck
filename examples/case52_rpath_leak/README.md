# Case 52: RPATH Leak (Hardcoded Build Directory)

**Category:** ELF / Deployment | **Verdict:** BAD PRACTICE

## What this case is about

Both libraries export identical symbols with identical signatures. The
difference is in the `DT_RUNPATH` / `DT_RPATH` metadata: v1 has a hardcoded
absolute path (`/home/build/myproject/lib`) baked into the binary, while v2
uses `$ORIGIN`-relative paths.

This is a **deployment-level bad practice**: build-directory paths in shared
libraries break on every machine except the one where the library was built.

## Real Failure Demo

**Severity: SECURITY / PACKAGING RISK**

The code path still runs, but v1 bakes a build-machine path into `RUNPATH`.
That can load unintended libraries on a matching host path or fail package
policy checks.

```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/abicheck-examples-build --target case53_rpath_leak_v1 case53_rpath_leak_v2

readelf -d /tmp/abicheck-examples-build/case53_rpath_leak/libv1.so | grep RUNPATH
# Library runpath: [/home/build/myproject/lib]

readelf -d /tmp/abicheck-examples-build/case53_rpath_leak/libv2.so | grep RUNPATH
# Library runpath: [$ORIGIN]
```

## Why hardcoded RPATH is bad practice

- **Non-portable:** The library only works if the exact build directory exists
  on the target machine. Deploying to another machine or container fails.
- **Security risk:** If an attacker can write to the hardcoded path
  (`/home/build/myproject/lib`), they can inject malicious libraries that get
  loaded by any binary using this RPATH.
- **Package manager conflicts:** Distribution packages must never contain
  hardcoded build paths. rpmlint and lintian flag this as a critical error.
- **Reproducibility:** Build artifacts contain host-specific paths, making
  reproducible builds impossible.

## What abicheck detects

- **`RPATH_LEAK`**: The library has `DT_RPATH` or `DT_RUNPATH` containing
  an absolute path that looks like a build directory. This is classified as
  a deployment metadata issue.

**Overall verdict: COMPATIBLE** (same ABI surface; RPATH is deployment concern).

## How to reproduce

```bash
# Build bad version (with hardcoded RPATH)
gcc -shared -fPIC -g bad.c -o libbad.so -Wl,-rpath,/home/build/myproject/lib

# Build good version (with $ORIGIN-relative RUNPATH)
gcc -shared -fPIC -g good.c -o libgood.so '-Wl,-rpath,$ORIGIN'

# Check RPATH
readelf -d libbad.so  | grep -E 'RPATH|RUNPATH'
# → (RUNPATH) Library runpath: [/home/build/myproject/lib]  ← leaked!
readelf -d libgood.so | grep -E 'RPATH|RUNPATH'
# → (RUNPATH) Library runpath: [$ORIGIN]  ← correct

# Run abicheck
python3 -m abicheck.cli dump libbad.so  -o /tmp/v1.json
python3 -m abicheck.cli dump libgood.so -o /tmp/v2.json
python3 -m abicheck.cli compare /tmp/v1.json /tmp/v2.json
# → COMPATIBLE + RPATH_LEAK warning
```

## How to fix

Use `$ORIGIN`-relative paths instead of absolute build paths:

```bash
gcc -shared -fPIC lib.c -o libfoo.so '-Wl,-rpath,$ORIGIN'
```

In CMake, use proper install RPATH:

```cmake
set(CMAKE_INSTALL_RPATH "$ORIGIN")
set(CMAKE_BUILD_WITH_INSTALL_RPATH OFF)
set(CMAKE_INSTALL_RPATH_USE_LINK_LIBRARIES OFF)
```

Or strip RPATH entirely and rely on system paths:

```bash
chrpath -d libfoo.so
# or
patchelf --remove-rpath libfoo.so
```

## Real-world example

Fedora's packaging guidelines explicitly forbid hardcoded RPATH. The
`check-rpaths` tool rejects any package with non-standard paths in
DT_RPATH/DT_RUNPATH. Debian's lintian reports `binary-or-shlib-defines-rpath`
as a warning for the same reason.

## References

- [Fedora: Packaging RPATH](https://docs.fedoraproject.org/en-US/packaging-guidelines/#_removing_rpath)
- [Debian: RPATH Policy](https://wiki.debian.org/RpathIssue)
