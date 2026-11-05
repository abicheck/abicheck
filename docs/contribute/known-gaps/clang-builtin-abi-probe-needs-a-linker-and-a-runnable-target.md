---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The clang frontend's builtin-ABI probe links and runs a program, so a cross or linker-less dump records no builtin `type_abi_fact`

Index: [Known gaps](../known-gaps.md)

**Status (2026-11-05): OPEN.**

`extract/builtin_abi.probe_builtin_abi` answers "what are the size and alignment of each builtin on this target" by compiling, linking and running a program that prints `sizeof`/`_Alignof` for each builtin, using the header frontend's own driver and options. It returns `None` when that program does not link or does not run, and then no builtin slot carries a fact.

Two ordinary situations hit that path:

- **No linker reachable.** Clang resolves `ld` from `PATH` (and from its GCC installation's target `bin` directory). In a container that ships the compiler but not binutils on `PATH`, the probe fails with `clang: error: unable to execute command: Executable "ld" doesn't exist!`.
- **A target the host cannot run.** A `--target=` cross dump, or the Windows/Mach-O ABI target the clang layout pass uses for a PE or Mach-O binary, builds a program the host cannot execute.

What the snapshot then says: every builtin `Param`/`TypeField`/`Variable.type_abi_fact` and `Function.return_type_abi_fact` reads `NOT_COLLECTED`, and `AbiSnapshot.typedef_abi_facts` is `None` (the producer captured none). Nothing is fabricated. The cost is that an ABI-equivalent retype (`long` to `long long` on LP64, say) is not downgraded to `abi_equivalent_type_changed` and stays a `type_field_type_changed`/`func_params_changed`. Record layout, enums, typedefs and vtables are unaffected, because the record-layout pass is compile-only.

Pinned by `tests/test_default_install_fact_invariants.py::test_clang_builtin_abi_facts_are_marked_without_a_linker`, which hides the linker and asserts the facts read `NOT_COLLECTED` while every other header family stays present. The test skips on a host where clang still finds a linker outside `PATH`.

**Fix direction.** A compile-only probe: one wrapper record per builtin (`struct W_int { int v; };`) dumped with `-Xclang -fdump-record-layouts -fsyntax-only` gives size and alignment for any target, with no link or run. Not done in the PR that found this: it measures member alignment rather than `_Alignof`, which differs on i386-like targets (`long long`, `double`), so it needs its own oracle and a decision on which alignment the fact records. Owner: `extract/builtin_abi.py`.
