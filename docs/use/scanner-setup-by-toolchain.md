---
doc_type: how-to
audience:
  - library-maintainer
  - ci-owner
level: intermediate
canonical_for:
  - scanner-setup-by-toolchain
summarizes:
  - client-toolchains
depends_on:
  - abicheck/extract/header_ast_backend.py
  - abicheck/model/header_ast_frontends.py
  - abicheck/buildsource/project_targets.py
lifecycle: active
generated: false
---

# Scanner Setup by Toolchain

What abicheck can check depends on what you give it, and on which client
compilers you promise to support. This page answers two questions: which
setup fits what you have, and how to cover more than one client toolchain.

## 1. Start from what you have

| You have | Evidence | What to install | What you get | What stays unchecked |
|---|---|---|---|---|
| The two binaries only | L0/L1 | nothing | exported symbols, versions, SONAME, binary-only removals | types and layout. Without type evidence on either side, an unchanged-looking pair reads `UNCONFIRMED` (exit 1), not a clean pass. |
| + debug info (DWARF/PDB) | types from the binary | nothing | compiled layout, signatures, member offsets | the header contract: macros, inline code, what clients actually see |
| + public headers (`-H`) | L2 | a header backend (below) | public surface, types, layout as a client compiler sees it | build-flag drift, source-only changes |
| + `compile_commands.json` or build data | L3 | the project's compiler | flag and toolchain drift, the header parse under the real flags | source-level behavior |
| + sources | L4/L5 | the project's compiler | inline, template and macro body changes | — |

The levels are explained in [Evidence &
Detectability](../learn/evidence-and-detectability.md). `--depth` is
covered in [Evidence Depth](evidence-depth.md).

**Before a real run**, `abicheck compare --dry-run` checks that the header
backend starts and parses your headers, and estimates the cost. During a
run, a backend that is missing or cannot bind names to exports does not
fail the comparison. The report shows it as `surface_scope.confidence:
reduced` with a reason (`header-backend-unavailable`,
`export-binding-unresolved`).

## 2. Pick a header backend

The backend is a configuration key, `compile.frontend` in `.abicheck.yml`.
There is no CLI flag. For a one-off run, set `ABICHECK_AST_FRONTEND`.

| `compile.frontend` | Install | Choose it when | Known gaps today |
|---|---|---|---|
| `castxml` (default) | CastXML | the default; most complete fact coverage, including record layout and member access | emulates the system `cc`/`c++`, so it sees *that* compiler's header branches only |
| `clang` | clang | Clang is your client compiler, or CastXML cannot parse the headers | record alignment, access on static data members (see the parity plan) |
| `gcc` | GCC + matching `gcc-N-plugin-dev`, plugins built once | GCC is your client compiler and you want GCC itself to read the headers (Linux ELF only, experimental) | several C attributes and C++ facts are not yet projected; see [Native GCC](native-gcc.md) |
| `hybrid` | both CastXML and clang | recovering facts each backend gets wrong alone | the cost of two parses |

The per-fact contract is in [Header-Backend
Capabilities](../reference/header-backend-capabilities.md). The measured
comparison of the three backends, and the criteria for changing the
default, are in the [header-backend parity
plan](../contribute/plans/header-backend-parity-and-client-matrix.md).

**Rule:** parse the headers with a backend of the same family as the
clients you care about. CastXML stands in for the system GCC. Native GCC
*is* GCC. Clang is Clang.

## 3. One library, several client toolchains

If your public headers depend on the compiler, the standard or the standard
library, each client toolchain sees its own ABI. [Client
Toolchains](../learn/client-toolchains.md) explains why. Check each
supported client separately, then read the strictest result.

### A single local check per client

Dump and compare once per client, each with its own backend and flags.
Keep both sides of a comparison under the same client context:

```bash
ABICHECK_AST_FRONTEND=gcc   abicheck compare old/libfoo.so new/libfoo.so -H include/ -o json=gcc-clients.json
```

```bash
ABICHECK_AST_FRONTEND=clang abicheck compare old/libfoo.so new/libfoo.so -H include/ -o json=clang-clients.json
```

Do not compare a snapshot taken for GCC clients with one taken for Clang
clients. abicheck refuses with exit `16` (`not_comparable`), because they
describe different contracts.

### Declaring client toolchains for CI

In a project configuration, each profile can carry a `consumer_compile:`
overlay: the compiler, standard, standard library and backend your clients
use, separate from the `compile:` block that describes how the library was
built.

```yaml
profiles:
  linux-gcc14-clients:
    contract: true
    compile:
      compiler_family: gcc
      binding: gcc14
    consumer_compile:
      compiler_family: gcc
      binding: gcc14
      standard: gnu++17
      frontend: gcc
  linux-clang20-clients:
    contract: true
    compile:
      compiler_family: gcc
      binding: gcc14
    consumer_compile:
      compiler_family: clang
      binding: clang20
      standard: c++20
      stdlib: libc++
      frontend: clang
```

Each profile becomes its own cell in the check matrix. The candidate
binary's headers are parsed under that profile's client context, and the
aggregate reconciles findings across profiles. Field reference:
[Project Targets Schema](../reference/project-targets-schema.md). End to
end: [Multi-Platform scenario](../integration/scenarios/multi-platform.md).

!!! warning "Current limits"
    The baseline (old) side of a baseline-channel check is not yet dumped
    under the consumer overlay. A compiler-version mismatch in a binding
    is caught by `project validate --toolchain-bindings`, not yet before
    extraction on `dump`/`compare`. Both items are tracked in the
    [parity plan](../contribute/plans/header-backend-parity-and-client-matrix.md).

## Checklist

- [ ] Give the richest evidence you have: headers at least, debug info if
      you ship it.
- [ ] Install the header backend that matches your client compiler, and
      confirm with `--dry-run`.
- [ ] List the client toolchains you support (compiler family, standard,
      standard library, target).
- [ ] More than one, or headers with compiler branches? Run one check per
      client.
- [ ] Treat `confidence: reduced` and `UNCONFIRMED` as "not checked", not
      as "compatible".
