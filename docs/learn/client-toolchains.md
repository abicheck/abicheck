---
doc_type: explanation
audience:
  - library-maintainer
  - ci-owner
level: intermediate
canonical_for:
  - client-toolchains
lifecycle: active
generated: false
---

# Client Toolchains: One Library, Several ABIs

A shared library is built once, by one compiler. Its **clients** compile
their own code against its public headers, and they may use other
compilers. The ABI a client depends on is the ABI *that client's compiler*
derives from those headers. Different clients can depend on different
ABIs, all from the same `.so`.

## Where the difference comes from

A public header is an input to every client's compiler. Anything in it that
reacts to the compiler can make two clients see two different contracts:

| Source of divergence | Example | What differs between clients |
|---|---|---|
| Compiler-detection branches | `#if defined(__clang__)`, `#if __GNUC__ >= 12`, `#ifdef _MSC_VER` | which members, types or declarations exist at all |
| Language standard gates | `#if __cplusplus >= 202002L`, `__STDC_VERSION__` | overloads, `constexpr`/`noexcept`, `std::span` vs. pointer+size parameters |
| Standard library | libstdc++ vs. libc++, `_GLIBCXX_USE_CXX11_ABI` | the layout and mangling of every `std::` type in a signature |
| Layout-affecting flags | `-fpack-struct`, `-malign-double`, `-fshort-enums`, `-funsigned-char` | record size, alignment, enum width, `char` signedness |
| Target and data model | `-m32` vs. `-m64`, ILP32 vs. LP64 | `long`, pointer and `size_t` widths |
| Implementation-defined layout | bit-field allocation, empty-base and `[[no_unique_address]]` handling | offsets that each compiler is free to choose |

A worked example. One header, one library built with GCC:

```c
#include <stddef.h>

struct msg {
#if defined(__clang__)
  long long id;
#else
  int id;
#endif
  char tag;
};
size_t msg_size(const struct msg *);
```

On x86-64 (LP64, 8-byte-aligned `long long`), a GCC client sees an 8-byte
`struct msg` and a Clang client a 16-byte one. The exact sizes depend on the
target; that they differ does not. Neither is wrong; they are two contracts. The library itself was
compiled with the GCC branch, so a Clang client that passes its own
`struct msg` into the library is already broken. No change between v1 and
v2 is needed for that.

## Three questions, not one

When a library supports more than one client toolchain, "is v2 compatible?"
splits into separate questions:

1. **Per client, over time:** for GCC clients, did v1 → v2 break them? For
   Clang clients, the same question asked separately. Each is an ordinary
   old-versus-new comparison, made entirely inside one client's view of the
   headers.
2. **Across clients, at one version:** do the GCC view and the Clang view
   agree with each other, and with the binary as it was built? This is a
   portability question about the header, not a release-compatibility
   question. Comparing the GCC view of v1 with the Clang view of v2 mixes
   the two questions and answers neither.
3. **Which clients do we promise to support?** This is a contract decision.
   It belongs in configuration, not in whatever compiler happened to be
   installed on the CI runner.

A release check answers question 1 once for each supported client. The
overall result is the strictest of them: a change that breaks only Clang
clients is still a break, reported against Clang clients.

## Why checking with one compiler is not enough

Any checker that reads headers reads them *as some compiler would*. It sees
one client population and is silent about the others. Silent is the
dangerous part: the report looks clean, and a change confined to the
`__clang__` branch, or to the C++20 overloads, or to the libc++ layout,
never enters it.

The rule of thumb:

- **One supported client toolchain** (the same family and standard library
  as the build): one check is enough.
- **Compiler-dependent headers, or a promise to several client
  toolchains:** check once per supported client. Declare each one with its
  compiler, standard, standard library and target. Read the verdicts per
  client, then take the strictest.
- **Header-only or template-heavy libraries:** everything happens in the
  client's compiler, so the per-client split matters most here. See
  [Static & Header-Only Contracts](static-and-header-only.md).

## How this relates to the other contract axes

- [Consumer Models](consumer-models.md) asks *what kind* of consumer you
  have (dynamic linker, plugin host, header-only user, ...). Client
  toolchain asks *which compiler* that consumer uses. The two are
  independent.
- [Build Profile Comparability](build-profile-comparability.md) says two
  snapshots can only be compared when they were taken under one compile
  context. That is why each client is compared with itself over time, not
  with another client.
- [Modern C/C++ and Toolchain ABI Hazards](modern-cpp-toolchain-hazards.md)
  covers the individual mechanisms (dual ABI, char signedness, packing) in
  depth.

For the practical side (which scanner setup fits the tools you have, and
how to declare several client toolchains), see [Scanner Setup by
Toolchain](../use/scanner-setup-by-toolchain.md).

---

**Ladder:** ← [Build Profile Comparability](build-profile-comparability.md) · Step 5 · Define Your Contract · [Static & Header-Only Contracts](static-and-header-only.md) →
