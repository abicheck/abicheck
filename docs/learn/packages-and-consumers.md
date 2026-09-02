---
doc_type: explanation
audience:
  - distribution-maintainer
  - library-maintainer
level: advanced
canonical_for:
  - packages-and-consumers
summarizes:
  - project-integration
depends_on:
  - abicheck/package.py
  - abicheck/debian_symbols.py
  - abicheck/scan_abi3_resolve.py
lifecycle: active
generated: false
---

# Packages and Consumers

The series so far compared a binary you built against a binary you built.
A distribution, a package index or a binding author meets the library as
an *artifact somebody else produced* — an RPM, a `.deb`, a conda package, a
wheel — and its consumers are often not C or C++ callers at all. This page
is for that audience: check the artifact that actually ships, and reason
about consumers that bind to the C ABI from another language.

## The artifact is the package

`compare` takes package files as operands and extracts them itself; the
debug and development packages are the evidence sources, attached per
side:

```bash
abicheck compare old.rpm new.rpm \
  --debug-info old=old-debuginfo.rpm --debug-info new=new-debuginfo.rpm \
  --devel-pkg old=old-devel.rpm --devel-pkg new=new-devel.rpm
```

A tarball or a conda package is the same command with a different
operand, since only the container changes:

```bash
abicheck compare old.tar.gz new.tar.gz -H include/
abicheck compare old.conda new.conda -H include/
```

A package-only check is a project of its own when there are several
targets: extract into the build-output layout and the ordinary project
checks apply unchanged. That is the
[package-only scenario](../integration/scenarios/packages-and-sdks.md)
of the [project integration](../integration/concepts.md) layer.

## Debian `symbols` files are a consumer-declared contract

A Debian `symbols` file records, per exported symbol, the *minimum package
version* a consumer built against it must depend on — a contract stated
from the packager's side, and a second opinion on the binary's own export
table. abicheck generates one from a shared library, validates a file
against a binary, and diffs two files; on a `.deb` compare it checks both
sides' files automatically and folds a mismatch into the warnings without
changing the verdict, since packaging drift and an ABI break are different
questions:

```bash
abicheck compare old.deb new.deb \
  --debug-info old=old-dbgsym.deb --debug-info new=new-dbgsym.deb
```

The commands, tag syntax and limits are owned by
[Debian Symbols File Integration](../use/debian-symbols.md).

## conda: the pieces live in different packages

A conda-forge library ships its runtime, headers and debug information as
separate packages under one feedstock, so the first job is to assemble one
side from several files; the second is to map the conda version string
back to the upstream tag it was built from, so that the baseline is the
release the package claims to be. With no single public header, the public
surface is passed as an umbrella header that includes the ones the package
exports. [Scanning a Conda-Forge Package](../start/scanning-conda-packages.md)
walks all three steps and the packaging shapes that need a workaround.

## Python extensions

An extension module exports one symbol, its init function, so its export
table says nothing about compatibility. The surface that decides whether
the module *loads* is the CPython C-API it imports, and the contract on
that surface is the limited API: a module tagged `abi3` promises to import
only the stable subset available since a stated Python version. The
one-build audit checks the promise against the binary:

```bash
abicheck scan mymod.abi3.so --abi3 3.9
```

Two more surfaces follow. The Python-level API — the functions, classes
and signatures a caller imports — is not in the binary at all, and a
renamed keyword argument breaks every caller while the C ABI is unchanged;
abicheck recovers that surface from the module's type stub
([case163](../reference/examples/case163_python_kwarg_renamed.md)). And a
wheel's `manylinux` tag is a glibc floor by another name: the tag promises
the wheel loads on any distribution with at least that glibc, which is the
same floor [Dependency & Runtime Floors](dependency-floors.md) explains.
All three are owned by
[Python Extension Modules](../use/python-extensions.md).

## FFI consumers

A Rust `extern "C"` block, a Go `cgo` preamble, a Python `ctypes`
signature: each is a *copy* of the C declaration, written by the consumer
and compiled into it. Nothing checks that copy against the library's
header at build time, so the binding is bound to the ABI as it was when
the copy was made. Two consequences. The direction of the promise is the
usual one — the library must keep what the copy assumes
([Compatibility Direction](compatibility-direction.md)) — but the copy can
be wrong from the start, and a check against the *header* would pass while
the binding is broken. And the consumer-scoped check has to match how the
binding binds: `--used-by` scopes to the imports recorded in an application
binary, so it fits a binding linked against the library at build time, but
a `ctypes` or `dlopen`-based binding resolves its names at runtime and the
interpreter that loads it imports none of them. State those names as the
contract instead ([Consumer Models](consumer-models.md)):

```bash
abicheck compare old.so new.so --required-symbol foo_open --required-symbol foo_read
```

## Kernel and accelerator ABIs

Two more ABI domains have their own consumers and their own evidence, and
the series only points at them. A kernel module binds to the kernel's
exported symbols through BTF/CTF type information, with kABI CRCs and
symbol namespaces as the contract: a struct field added in the kernel's own
types ([case121](../reference/examples/case121_kernel_btf_struct_field_added.md)),
a CRC change ([case175](../reference/examples/case175_kabi_crc_changed.md)),
an export moved to another namespace
([case176](../reference/examples/case176_kabi_symbol_namespace_changed.md)),
owned by [Kernel BTF/kABI](../use/kernel-btf.md). A SYCL library has a
host ABI and a device-side one, and a DPC++ build withdrawn or an
implementation pointer changing shape breaks the host contract in ways an
ordinary C++ comparison would miss
([case82](../reference/examples/case82_sycl_overload_set_removed.md),
[case126](../reference/examples/case126_sycl_device_impl_ptr.md)).

---

**Ladder:** ← [Environment & Toolchain Drift](environment-drift.md) · Tier 7 · At scale · [Behavioral & Semantic Compatibility](behavioral-compatibility.md) →
