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
| + public headers (`-H`) | L2 | a header backend (below) | public surface and types as a client compiler sees them; layout from the backend where debug info does not already give the producer's layout | build-flag drift, source-only changes |
| + `compile_commands.json` or build data | L3 | the project's compiler | flag and toolchain drift, the header parse under the real flags | source-level behavior |
| + sources | L4/L5 | the project's compiler | inline, template and macro body changes | — |

The levels are explained in [Evidence &
Detectability](../learn/evidence-and-detectability.md). `--depth` is
covered in [Evidence Depth](evidence-depth.md).

**Before a real run**, `abicheck compare --dry-run` shows which header
backend the run will use and whether its tool is installed, and estimates
the cost. It does not parse your headers. During a run:

- **ELF:** a header pass that needs CastXML fails before extraction when
  CastXML is missing.
- **PE and Mach-O:** a missing backend, or headers whose declarations
  cannot be bound to the exports, fall back to the export table. The report
  shows `surface_scope.confidence: reduced` with a reason
  (`header-backend-unavailable`, `export-binding-unresolved`).

## 2. Pick a header backend

The backend is a configuration key, `compile.frontend` in `.abicheck.yml`.
There is no dedicated CLI flag; for one run, pass `--set
compile.frontend=<backend>`. The `ABICHECK_AST_FRONTEND` environment
variable applies only when the configuration sets no `compile.frontend`.

| `compile.frontend` | Install | Choose it when | Known gaps today |
|---|---|---|---|
| `castxml` (default) | CastXML | the default; most complete fact coverage | emulates the system `cc`/`c++`, so it sees *that* compiler's header branches only; drops `= delete`d members, elides defaulted template arguments and inline namespaces from names, records no data size |
| `clang` | clang | Clang is your client compiler, or CastXML cannot parse the headers | sees only explicitly instantiated template specializations (see the parity plan) |
| `gcc` | GCC + matching `gcc-N-plugin-dev`, plugins built once | GCC is your client compiler and you want GCC itself to read the headers (Linux ELF only, experimental) | experimental: partial assurance, no template overload constraints or macro comparison; see [Native GCC](native-gcc.md) |
| `hybrid` | both CastXML and clang | recovering facts each backend gets wrong alone | the cost of two parses |

The per-fact contract is in [Header-Backend
Capabilities](../reference/header-backend-capabilities.md). The measured
comparison of the three backends, and the criteria for changing the
default, are in the [header-backend parity
plan](../contribute/plans/header-backend-parity-and-client-matrix.md).

**Rule:** parse the headers with a backend of the same family as the
clients you care about. CastXML emulates whatever the system `cc`/`c++`
is, which may be GCC or Clang; check which before letting it stand in for
GCC clients. Native GCC *is* GCC. Clang is Clang.

## 3. One library, several client toolchains

If your public headers depend on the compiler, the standard or the standard
library, each client toolchain sees its own ABI. [Client
Toolchains](../learn/client-toolchains.md) explains why. Check each
supported client separately, then read the strictest result.

### A single local check per client

Dump and compare once per client, each with its own backend and flags.
Keep both sides of a comparison under the same client context:

```bash
abicheck compare old/libfoo.so new/libfoo.so --header old=old/include --header new=new/include --set compile.frontend=gcc -o json=gcc-clients.json
```

```bash
abicheck compare old/libfoo.so new/libfoo.so --header old=old/include --header new=new/include --set compile.frontend=clang -o json=clang-clients.json
```

Do not compare a snapshot taken for GCC clients with one taken for Clang
clients: they describe different contracts. abicheck refuses such a pair
with exit `16` (`not_comparable`) when both snapshots record their compile
context; an older snapshot without one cannot be checked this way.

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
    A compiler-version mismatch in a binding is caught by `project
    validate --toolchain-bindings`, not yet before extraction on
    `dump`/`compare`. This is tracked in the
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
