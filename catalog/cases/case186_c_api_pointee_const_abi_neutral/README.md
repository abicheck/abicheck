# Case 186: C API Pointee const-Qualification — ABI-Neutral, Not Source-Neutral

**Category:** Risk | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

## Verdict and consumer impact

`send_buffer()`'s parameter changes from `char *` to `const char *`. The
pointer itself is still one machine word, passed the same way, with the
same calling convention — only the pointee's mutability contract tightened
(the callee now promises not to write through the pointer). Already-built
binaries keep working, and every *direct* call — passing a mutable or an
already-const buffer — still compiles against v2.

It is not a no-op at the source level. The function's **type** changed from
`void (char *)` to `void (const char *)`, so a consumer that stores it in a
function pointer of the old type — a callback table, a plugin registration,
`void (*cb)(char *) = send_buffer;` — no longer builds: a constraint
violation in C (GCC 13 warns, GCC 14+ and Clang with
`-Werror=incompatible-function-pointer-types` reject it) and an error in
C++. Strict C11 GCC and Clang were confirmed to accept the assignment
against v1 and reject it against v2. Whether any consumer uses the entry
point that way is not visible from the library, so abicheck reports a
**conditional risk** (`param_pointee_qualifier_added`,
`COMPATIBLE_WITH_RISK`, exit 0). A project that promises source
compatibility for function-pointer use can gate it.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `void send_buffer(char *data);` | `void send_buffer(const char *data);` |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so
cat > .abicheck.yml <<'EOF'
compile:
  frontend: clang
EOF
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h --config .abicheck.yml
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE_WITH_RISK (exit 0)

param_pointee_qualifier_added: Parameter pointee qualifier added: send_buffer param data: char * → const char *
```

`func_params_changed` must *not* fire — the calling convention is unchanged
(this is the Wayland `wl_display` false-positive class this case was
created for). `param_pointee_qualifier_changed` must not fire either: that
kind is for the direction that breaks *direct* callers (a qualifier removed,
or added below the first pointer level, e.g. `char **` → `const char **`).
The case's `source_smoke` proves the function-pointer condition with GCC and
Clang.

## Minimum evidence

`min_evidence: L2` — a mechanical type-spelling diff would need only DWARF
to see `char *` vs `const char *` as differing strings and misreport a
break; the public header AST is what lets abicheck recognize the top-level
`*`-plus-`const`-only shape, keep it out of the binary-signature detector
and report the source-level condition with the right direction. castxml is the documented default backend for this
evidence layer; clang (`compile.frontend: clang` (via `.abicheck.yml`), used above) is a supported
alternative AST frontend for hosts without castxml installed.

## Why abicheck catches it

`cv_qualifiers_only_differ()` recognizes a parameter pair with a top-level
`*`/`&` that differs only by `const`/`volatile` on or behind it, so the
binary-signature detector (`FUNC_PARAMS_CHANGED`) stays silent. The
source-level effect is decided separately by
`compare/parameter_facts.py`'s `pointee_qualifier_changes()`: a qualifier
gained by the single pointee is `param_pointee_qualifier_added` (risk —
function-pointer consumers), anything else (removed, or gained deeper) is
`param_pointee_qualifier_changed` (API break — direct callers). Both are
header-tier findings; a DWARF-only side carries no reliable qualifier
spelling.

## Runtime failure demonstration

**Severity: none for already-built binaries — verified.**

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -g app.c -L. -lv1 -Wl,-rpath,. -o app
./app            # sent 5 bytes
gcc -shared -fPIC -g v2.c -o libv1.so   # swap in v2, no recompile
./app            # sent 5 bytes   (identical output)
```

The pointer is still passed in the same register with the same width. The
break is a rebuild-time one for function-pointer consumers, shown by the
case's source smoke rather than the runtime swap.

## Safe redesign

For direct callers this is the safe direction. If consumers are known (or
promised) to register the entry point as a callback, keep the old signature
and add the const-correct variant under a new name, or document the change
as a source break for function-pointer users. Adding `const` to a public
struct **field** is a different, harder case: code that writes through the
field stops compiling for every consumer that does so
(`field_became_const`, `case30_field_qualifiers`).

**Real-world example:** Wayland's `wl_display` accessor functions picked up
pointee `const` on their parameters between releases without an actual ABI
break, and conda-forge's libuv 1.5x packaging campaign hit the false-positive
class of reporting that as a BREAKING parameter change — the reason it is a
non-gating risk here, not `FUNC_PARAMS_CHANGED`.

## Cross-tool comparison

A naive AST- or symbol-spelling diff (including some `abidiff`
configurations) reports this as a signature change requiring investigation,
since `char *` and `const char *` are different type strings; abicheck's
header-aware `cv_qualifiers_only_differ()` check is what tells the two
apart from a real pointee-type change (see
[`case46_pointer_chain_type_change`](../case46_pointer_chain_type_change/README.md)
for the non-suppressed baseline).

## References

- [`tests/test_const_pointer_abi_neutral.py`](../../tests/test_const_pointer_abi_neutral.py) — unit-level equivalent
- [`tests/test_libuv_private_type_churn.py`](../../tests/test_libuv_private_type_churn.py) — the struct-field negative case
