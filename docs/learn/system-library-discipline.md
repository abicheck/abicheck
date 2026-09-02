---
doc_type: explanation
audience:
  - library-maintainer
  - distribution-maintainer
level: advanced
canonical_for:
  - system-library-discipline
depends_on:
  - abicheck/policies/glibc_symbol_versioned.yaml
  - abicheck/diff_versioning.py
lifecycle: active
generated: false
---

# How System Libraries Stay Compatible

glibc has shipped one SONAME, `libc.so.6`, since 1997. libstdc++ has shipped
`libstdc++.so.6` since 2004, through a change to the layout of
`std::string` that would have been a break for any other library. Neither
achieved this by never changing; both did it by adopting a *discipline* —
a strategy for evolving the contract that a library of any size can copy.
This page is that strategy, placed on a ladder with the others.

## The ladder of strategies

| Tier of the stack | Example | Strategy | Check it with |
|---|---|---|---|
| kernel ↔ user space | syscalls, kABI | never break; exported-symbol namespaces and CRCs | [Kernel BTF/kABI](../use/kernel-btf.md); [case175](../reference/examples/case175_kabi_crc_changed.md), [case176](../reference/examples/case176_kabi_symbol_namespace_changed.md) |
| C runtime | glibc | one SONAME for decades; every ABI change is a *new version node*, the old node kept as a compat symbol; append-only | `--policy glibc_symbol_versioned`; [case13](../reference/examples/case13_symbol_versioning.md), [case65](../reference/examples/case65_symbol_version_removed.md), [case139](../reference/examples/case139_symbol_version_node_removed.md), [case141](../reference/examples/case141_versioned_symbol_scheme.md), [case183](../reference/examples/case183_internal_version_node_churn.md) |
| C++ runtime | libstdc++ | same SONAME since 2004; `GLIBCXX_3.4.x` nodes; the dual ABI as a *parallel* namespace, not a break | [case104](../reference/examples/case104_glibcxx_dual_abi_flip.md); [Modern C/C++ and Toolchain Hazards](modern-cpp-toolchain-hazards.md) |
| system tooling | binutils / `ld` | linker defaults drift between releases and move a library's contract without a source change | [Environment & Toolchain Drift § binutils](environment-drift.md#the-binutils-side-linker-default-drift); [Security Hardening](../use/security-hardening.md) |
| vendor SDK / product | oneDAL, TBB, OpenSSL | SONAME bump per major; inline-namespace generations; explicit-instantiation matrix; experimental namespaces | [Part 7](abi-series/07-designing-for-stability.md); [case99](../reference/examples/case99_experimental_graduated.md)–[case101](../reference/examples/case101_inline_namespace_version_bumped.md); [Template- and Header-Heavy Libraries](template-heavy-libraries.md) |
| application plugin | host ↔ `dlopen` | required-symbol contract, direction reversed | `--policy plugin_abi`, `--required-symbol`; [Consumer Models](consumer-models.md) |

Find your own library's row. Most libraries are the fifth; the discipline
of the second and third rows is what the rest of this page explains,
because it is the one that makes "one SONAME for decades" possible.

## glibc: one SONAME, append-only version nodes

A version script groups exported symbols into named nodes, recorded in the
library and referenced by every consumer's own binary
([Part 5 § Symbol versioning](abi-series/05-linker-elf.md#3-symbol-versioning)).
glibc's rule is that a node, once shipped, is never removed and never
changes: when `realpath` needed a different behaviour, glibc added
`realpath@@GLIBC_2.3` and kept `realpath@GLIBC_2.0` as a *compat symbol*
that old binaries keep resolving. The whole ABI history is additive, and
the SONAME never has to move.

What a consumer records is the *highest node it needed*: a binary linked
against glibc 2.28 carries `GLIBC_2.28` in its version requirements, and
that requirement is the deployment floor — it loads on any glibc that
has the node and refuses on any that does not. The floor is a property of
the consumer's build, not its source, which is why a mere relink on a
newer host can raise it
([Dependency & Runtime Floors](dependency-floors.md)).

The cases mark the rules' edges: adding a version script to an unversioned
library is compatible, because old binaries carry no requirement
([case13](../reference/examples/case13_symbol_versioning.md)); removing a
node strands every binary that named it
([case65](../reference/examples/case65_symbol_version_removed.md),
[case139](../reference/examples/case139_symbol_version_node_removed.md));
renaming the scheme wholesale is the same break for the whole library
([case141](../reference/examples/case141_versioned_symbol_scheme.md)); and
churn in a node that only internal symbols use is risk, not a break,
because no consumer could have recorded it
([case183](../reference/examples/case183_internal_version_node_churn.md)).

## libstdc++: the dual ABI as a parallel namespace

libstdc++ applied the same append-only rule to C++, where the hard case is
not a function but a *type*. C++11 required `std::string` and `std::list`
to change layout. Instead of a new SONAME, GCC 5 put the new types in an
inline namespace, `std::__cxx11`, so their mangled names differ from the
old ones and both sets of symbols coexist in one library; a translation
unit chooses with `_GLIBCXX_USE_CXX11_ABI`. It is
[Part 7 Pattern 5](abi-series/07-designing-for-stability.md#pattern-5-inline-namespaces-for-generational-c-abi)
at the scale of a whole standard library.

The cost is that the *choice* became part of every library's contract: a
library rebuilt with the other setting exports different mangled names
for every function that takes a `std::string`, and its consumers stop
linking. That flip is what the report shows for
[case104](../reference/examples/case104_glibcxx_dual_abi_flip.md), as a
single root-cause finding rather than hundreds of removals; the wider
family of toolchain-level hazards is
[Modern C/C++ and Toolchain Hazards](modern-cpp-toolchain-hazards.md).

## binutils and the linker: defaults move the contract

The fourth row is the one nobody designs. A newer binutils flips a linker
default — packed relative relocations that need a newer loader, `DT_RPATH`
versus `DT_RUNPATH`, the hash-table style, CET or static-TLS markings, a
distribution's RELRO default — and a library rebuilt with no source change
ships a different contract. The detector family and each flag's fix are in
[Environment & Toolchain Drift § binutils](environment-drift.md#the-binutils-side-linker-default-drift);
the hardening flags in [Security Hardening](../use/security-hardening.md).
The discipline here is reproducibility: pin the toolchain that builds a
release, and compare the release against the previous one rather than
against a rebuild.

## Adopting the discipline yourself

- **One version node per release that changes the ABI**, in the version
  script ([Part 7 Pattern 4](abi-series/07-designing-for-stability.md#pattern-4-version-scripts-visibility-own-your-export-surface)):
  new symbols go in a new node that inherits the old; old nodes are never
  edited.
- **Change a function's contract by adding a versioned alias**, not by
  editing it: the new implementation becomes the default version and the
  old one stays exported under its old node (`.symver` in GNU assembler
  syntax, or the compiler's `symver` attribute).
- **Remove only on a SONAME bump.** A symbol or node is deleted only when
  the library's identity changes, so that no binary linked against the old
  identity can reach the new one. OpenSSL 3.0's node removals forced
  exactly that bump.
- **For C++, use inline-namespace generations**
  ([Part 7 Pattern 5](abi-series/07-designing-for-stability.md#pattern-5-inline-namespaces-for-generational-c-abi)):
  a new generation is added beside the old, and bumping the generation is
  itself a deliberate break
  ([case101](../reference/examples/case101_inline_namespace_version_bumped.md)).
- **Keep an experimental namespace** for surface that has not earned a
  promise: graduating from it is compatible
  ([case99](../reference/examples/case99_experimental_graduated.md)), and
  removing an experimental declaration without a replacement is still a
  break for anyone who used it
  ([case100](../reference/examples/case100_experimental_removed_without_replacement.md)).

## Checking it

The built-in policy encodes the glibc rules: a version-node removal is
pinned to a break under any base policy, a consumer's *added* compat
version requirement is accepted as the way a rebuilt consumer is expected
to evolve, and a dropped `DT_NEEDED` is surfaced as deployment risk:

```bash
abicheck compare libfoo.so.1.old libfoo.so.1.new --policy glibc_symbol_versioned
```

The one-build audit checks the discipline's own hygiene: an export with no
version node in a library that otherwise versions everything is a symbol
no consumer can pin
([case145](../reference/examples/case145_audit_unversioned_export.md)):

```bash
abicheck scan libfoo.so.1 -H include/
```

Profile contents and the other ecosystem profiles are owned by
[Policy Profiles](../use/policies.md#built-in-use-case-profiles).

---

**Ladder:** ← [Template- and Header-Heavy Libraries](template-heavy-libraries.md) · Tier 7 · At scale · [Dependency & Runtime Floors](dependency-floors.md) →
