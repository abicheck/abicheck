# Case 182: Accidental Export Removed — Still Breaking Under Public-Header Scoping

**Category:** Breaking | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

`internal_helper()` is exported by the v1 library (default visibility, no
`-fvisibility=hidden`, no version script) but never declared in `v1.h` — an
accidental export, the same pattern audited by
[case143_audit_accidental_export](../case143_audit_accidental_export/README.md).
v2 inlines its logic into `public_api()` and drops the symbol entirely.
`public_api()` itself is unchanged, so a consumer that only calls the
documented API notices nothing. But the absence of a header declaration
proves `internal_helper` wasn't part of the *documented* contract — it does
not prove nobody depends on it. A consumer that obtained the symbol via
`dlsym()`, a leaked internal header, or a hand-written prototype fails at
lookup time once v2 removes it.

## Old/new diff

| v1.h / v1.c | v2.h / v2.c |
|-------------|-------------|
| `int public_api(int x);` (declared) | `int public_api(int x);` (unchanged) |
| `int internal_helper(int x) { return x * 2; }` *(exported, undeclared)* | *(removed — inlined into `public_api`)* |
| `int public_api(int x) { return internal_helper(x) + 1; }` | `int public_api(int x) { return x * 2 + 1; }` |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libfoo_v1.so
gcc -shared -fPIC -g v2.c -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so -H v1.h --ast-frontend clang
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- func_removed_elf_only: Elf_only function removed: internal_helper
  > Exported function symbol removed from the binary; old binaries that
    link or dlsym() it can fail even without header evidence.
```

`--scope-public-headers` is on by default and changes nothing here: nothing
is filtered, because nothing can be proven non-public. (Comparing the same
two `.so` files with no header at all, `-H` omitted entirely, reports the
same underlying fact as a plain `func_removed`.)

## Minimum evidence

`min_evidence: L0` — the raw fact (a real, exported function symbol
disappeared) is visible from the ELF dynamic symbol tables alone; the
header above is not needed to *detect* the break, only to demonstrate that
public-header scoping — which filters changes it can *prove* are
unreachable from the public surface — does not launder this one away. A
bare ELF export with no header declaration at all cannot be proven
non-contractual, so it stays reported.

## Why abicheck catches it

Public-surface scoping filters changes to types/symbols it can prove are
unreachable from the public-header surface — an internal struct's layout
is only observable by a caller that names the type. But `internal_helper`
is a real, exported, callable symbol; its absence from `v1.h` is not
positive proof that nothing depends on it (it could still be reached via
`dlsym()` or direct linkage). abicheck's authority model never lets an
absence of positive evidence override negative evidence already proven at
the ELF layer — a real, exported function that just disappeared — so the
removal is folded back in and reported as BREAKING regardless of scoping.

That reconciliation (`fold_l0_hard_removals()`) is CLI-layer plumbing
invoked from the full `abicheck compare` pipeline shown above, not from the
lower-level `checker.compare()` API a faster test harness might call
directly — since `internal_helper` has no header declaration on either
side, a direct `dump()`+`compare()` call never learns it existed and would
report `NO_CHANGE`. The command above is the real, user-facing CLI path,
which is what actually ships this BREAKING verdict.

## Runtime failure demonstration

**Severity: BREAKING for any consumer reaching for the undocumented export.**

The shipped `app.c` only calls the documented `public_api()`, which stays
correct in both versions — deliberately illustrating that the "obvious"
consumer notices nothing:

```bash
gcc -shared -fPIC -g v1.c -o libfoo.so
gcc -g app.c -I. -L. -lfoo -Wl,-rpath,. -o app
./app
# → public_api(5) -> 11

gcc -shared -fPIC -g v2.c -o libfoo.so
./app
# → public_api(5) -> 11   (still fine — public_api's own contract never moved)
```

A consumer that instead reaches for the undocumented export directly (a
minimal `dlsym()` probe, not part of this case's checked-in fixtures) does
fail once v2 removes it:

```bash
./dlsym_demo   # dlopen("./libfoo.so"), dlsym("internal_helper")
# against v1: internal_helper(5) = 10
# against v2: dlsym(internal_helper) FAILED: ./libfoo.so: undefined symbol: internal_helper
```

**Why BREAKING:** the symbol was real, exported, and callable in v1; a
consumer that resolved it by name at load time or link time has no way to
know it wasn't "supported" — the header alone cannot prove that.

## Safe redesign

If `internal_helper` truly has no external callers, ship the removal with a
major version bump / SONAME change to signal the break. If it must stay
ABI-stable for `dlsym()`-based consumers, keep the symbol exported (even as
a thin compatibility shim). Better yet, mark it `static` or hidden-visibility
from the start so it never becomes an accidental export in the next release
(see case06 for the visibility-based fix).

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
```

`abidiff`/`abidw` are not installed in this environment, so no output is
reproduced here. `abidiff` has no notion of public-header scoping at all —
it treats every exported symbol as part of the ABI, so it would flag this
removal the same way regardless of the scoping question this case exists
to answer.

## References

- Related cases:
  [case143_audit_accidental_export](../case143_audit_accidental_export/README.md),
  [case118_internal_struct_field_added_scoped](../case118_internal_struct_field_added_scoped/README.md),
  [case164_preproc_conditional_field](../case164_preproc_conditional_field/README.md)
