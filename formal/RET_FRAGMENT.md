# A closed return-type fragment, with a calling-convention oracle

This is the fourth fragment closed end to end (E14), after record layout
([`FRAGMENT.md`](FRAGMENT.md), E10), enums
([`ENUM_FRAGMENT.md`](ENUM_FRAGMENT.md), E12) and function parameters
([`FUNC_FRAGMENT.md`](FUNC_FRAGMENT.md), E13). Like E13 it has two oracles:

* **The report contract.** This is what abicheck should report for a
  return-type change.
* **The call interface.** This asks whether an old binary still calls the new
  function correctly. A return-type change can break it in two places: the
  result, and the arguments. Under x86-64 System V a struct over 16 bytes is
  returned through caller memory whose address the caller passes as a hidden
  first argument in `rdi` ("sret"), so every INTEGER argument moves up one
  register when that hidden argument appears or disappears. A model of both
  is proved in Lean, and a real old caller calling the new library checks it
  on every case.

The two disagree on 168 cases (see [Findings](#findings)).

**The claim, exactly.** For the space **R** defined below:

- every case of R has exactly one generated program pair, and every generated
  pair is in exactly one case of R (proved);
- the arguments and the call interface survive a change exactly when the
  theorems of part 4 say so, for parameter lists of any length (proved, for
  the model);
- gcc 13.3 and clang 18.1 behave as the model on every case (tested by
  execution, 0 mismatches);
- abicheck reports exactly the report contract's findings and verdict on every
  case for both builds (tested, 0 mismatches);
- two cases have the same observable result (findings, verdict, call
  compatibility, argument placement) iff they have the same class key
  (proved). The 8-case reduced set contains one case of each class (tested).

**Files:**

- [`lean/AbiLean/RetFragment.lean`](lean/AbiLean/RetFragment.lean): the
  space, the generator, both oracles and the theorems. It reuses the
  parameter types, `locAt` and the declaration helpers of `FuncFragment`.
- [`lean/AbiLean/RetFragmentExport.lean`](lean/AbiLean/RetFragmentExport.lean):
  writes [`lean/ret_fragment_cases.json`](lean/ret_fragment_cases.json).
- [`experiments/ret_fragment.py`](experiments/ret_fragment.py): the
  generator, the execution oracle and the abicheck run.
- [`../tests/test_formal_ret_fragment.py`](../tests/test_formal_ret_fragment.py).

## The fragment R

| Axis | In R | Outside R |
|---|---|---|
| Language, ABI | C11, x86-64 System V | C++ (non-trivial classes, references), AArch64, Windows x64, i386 |
| Function | `R fN(…)` with 0–2 parameters `p0, p1`, each `int`, `long`, `double` or `void *`; parameters unchanged | parameter edits (E13), more than 6 INTEGER arguments |
| Return types | `void`, `int`, `long`, `double`, `void *`, `struct s2l {long a, b;}` (`rax:rdx`), `struct s2d {double a, b;}` (`xmm0:xmm1`), `struct s3l {long a, b, c;}` (memory) | `float`, `char`/`short`, `long double`, unions, mixed INTEGER/SSE structs, packed or non-trivial structs, `_Bool` |
| Edit | change the return type to another of the eight | a struct's own layout, several edits |
| Evidence | both sides `-g -O0`, public header supplied (castxml) | DWARF-only, header-only, stripped |
| Observable | abicheck's finding kinds and verdict; whether every argument arrives intact; whether an old caller receives the new result intact | report wording |

Size: |R| = (1 + 4 + 16) · 8 · 7 = **1176** cases.

## The five parts

### 1–2. Space and generator

Proved:

- `mem_space`
- `space_nodup`
- `space_length` (= 1176)
- `decode_gen`
- `gen_decode`
- `generated_in_exactly_one`
- `every_case_generated`

The C generator renders each prototype without its name, for example
`struct s3l (int p0, double p1)`. It is checked against the space and against
Lean's `gen`, with an FNV-1a-64 checksum over all cases plus the full text of
each representative.

### 3. The two oracles

**Report contract** (`findings`, `verdict`). Always `func_return_changed`.
In addition:

- `return_pointer_level_changed` where pointer-ness differs;
- `struct_return_convention_changed` where both sides return a struct and one
  is returned in registers, the other through memory.

All three are breaking by catalog default, so every verdict is breaking
(`every_case_breaking`).

**Call interface** (`argsIntact`, `resultIntact`, `callCompatible`).

- *Arguments*: argument `k` goes in SysV's register for it (`locAt`, from
  E13), moved up one INTEGER register when the function returns through
  memory.
- *Result*: an old caller of a `void` function reads nothing, but a new
  function returning through memory still writes through `rdi`, which that
  caller never set to a buffer. Any other old caller reads the result where
  its type is returned (`eax`, `rax`, `xmm0`, `rax:rdx`, `xmm0:xmm1`, caller
  memory) in that type's form, and the result is intact iff the new type is
  returned in the same place and form. In R that is only `long` ↔ `void *`.

A case is call-compatible iff both hold.

**Execution oracle** (`ret_fragment.py`). A caller is compiled against the old
header and linked against the new library. Each call runs in its own forked
child.

- The caller passes the marker arguments of E13. The new function records
  them in shared memory, so they are read even when the call then crashes.
- The new function returns a marker value of its type; integer markers lie
  outside 32 bits and the `int` marker is negative, so a width change is
  visible in both directions. The caller prints what it received as the old
  type.
- When the caller passes no INTEGER argument and has no sret of its own, it
  first points `rdi` at a sentinel buffer. A new function returning through
  memory then writes into the sentinel instead of a random address. This
  decides 21 cases, which would otherwise pass or fail by chance.

A case's arguments are intact iff every marker arrived. Its result is intact
iff the child did not crash, the sentinel is untouched, and an old non-`void`
caller received exactly the new function's value. When the new function is
`void`, an old non-`void` caller reads a stale register: that is not intact
by definition, since no value was returned.

### 4. Theorems

| Theorem | Statement |
|---|---|
| `args_intact_iff` | the arguments survive **iff the sret status is unchanged or no parameter is INTEGER** |
| `call_compatible_iff` | the call interface survives **iff the old type is `void` and the new one is not returned through memory, or both are returned in the same place and form** |
| `params_irrelevant` | the parameters never decide call compatibility: they matter only when the sret status changes, and then the result is broken anyway |
| `void_result_compatible_iff` | a `void` function may start returning any value but a memory-returned struct |
| `scalar_to_sret_moves_args` | `long f(long)` → `struct s3l f(long)` moves the argument, and is reported only as `func_return_changed` |
| `every_case_breaking` | the report contract calls every case breaking |
| `breaking_but_call_compatible` | `void f(int)` → `int f(int)` is breaking under the contract, and an old binary still calls it correctly |
| `obs_eq_iff_key` | same observable ⇔ same key (pointer-level flip, convention flip, call compatibility, argument placement) |

`args_intact_iff`, `call_compatible_iff` and `params_irrelevant` are stated
for parameter lists of any length.

### 5. Runs

```
                               gcc 13.3.0      clang 18.1.3
execution = model              1176 / 1176     1176 / 1176     (168 call-compatible, 126 calls crash, 21 sentinel writes)
abicheck  = report contract    1176 / 1176     1176 / 1176
findings on no fragment function    0               0
```

The full space takes 12 s for both compilers. The 8 classes give a reduced
set 147× smaller.

## Findings

**abicheck calls 168 return-type changes ABI breaks although every old binary
keeps working.** They are:

| Change | Cases | Why old binaries still work | Why source breaks |
|---|---|---|---|
| `void` → `int`, `long`, `double`, `void *`, `s2l`, `s2d` | 126 | the old caller never reads the result register | only where the function's type is used: `void (*fp)(int) = f;` no longer compiles; a plain call still does |
| `long` ↔ `void *` | 42 | same register, same 64-bit representation | an integer↔pointer conversion is a constraint violation in C |

abicheck reports all 168 as `breaking`, because `func_return_changed` is
breaking whatever the calling convention says. This is the same gap as E13's
240 parameter edits: an edit's verdict does not depend on the platform ABI.
It is recorded in
[`docs/contribute/known-gaps.md`](../docs/contribute/known-gaps.md), with the
same remedy (an ADR making the verdict ABI-aware). The `void` → value changes
are the strongest case: most callers' source still compiles.

`void` → `struct s3l` is correctly incompatible. The new function writes its
result through `rdi`. That register holds the first INTEGER argument, if
there is one, or else whatever the caller left in it. Across all seven
changes to `struct s3l`, the 126 calls that pass an INTEGER argument crash.
The 21 that do not write into the sentinel.

## Observations outside the observable

* **The struct-return convention finding depends on both sides being
  structs.** `struct_return_convention_changed` fires for `s2l` ↔ `s3l` and
  `s2d` ↔ `s3l`. For `long` → `s3l` it does not fire, although the hidden
  pointer moves the INTEGER arguments (`scalar_to_sret_moves_args`). The
  verdict is the same, since `func_return_changed` is breaking, but the
  report does not name the mechanism. The detector says so on purpose
  (`dwarf_advanced._diff_value_abi_traits`): a scalar side "can still be
  register-returned".
* **Its description shows the triviality trait, not the mechanism.** For
  `s2l` → `s3l` it reads `Aggregate return convention changed: fN
  (ret:trivial → ret:trivial)`. Both structs are trivial; what changed is
  registers → memory, which the text does not say.

* **The exact report match depended on `clang` being on PATH (fixed).**
  The header graph is built from `clang -ast-dump=json` when clang is
  available and from the CastXML-parsed snapshot otherwise. The two spell
  fundamental types differently (`long` vs `long int`), and the builtin
  filter knew only clang's spellings, so without clang `long int` became a
  graph node. Every case where one side uses `long` as both a parameter and
  the return (e.g. `void f(long)` → `long f(long)`) then carried an extra
  `public_dependency_role_changed` (`param` → `param, return`): 112 of 1176
  cases, first ids 113, 120, 126, 127, 128. Call observations and verdicts
  were unaffected. The filter now recognises any spelling made only of
  fundamental-type keywords; both lanes give 1176/1176 exact matches.
  Regression: `tests/test_type_graph_names.py` (every permutation of
  C11 6.7.2p2's specifier multisets). To run the CastXML lane, put a PATH
  without `clang` in front of the experiment.

## Struct member layouts (E14b)

E14 compares struct returns of *different* classes only. A change that keeps
the class -- `{long, long}` to `{int, int, long}`, both `rax:rdx` -- needs the
member layout, which `abicheck.model.sysv_aggregate` derives from the header's
record layout (L2: size, alignment, field offsets, field ABI facts, nested
records, pointer arrays, bitfields; no DWARF).

**Space.** Every struct of one to three `int`/`long`/`double` members under
natural layout whose SysV return class is one of INT1, INT2, SSE1, SSE2 or
MEMORY (mixed INTEGER/SSE structs are outside), plus two 32-byte structs so a
memory result can grow: 35 structs, 1190 ordered pairs, returned from
`struct R f(void)`. A member's name is its type and byte offset (`l8`), so it
keeps its name exactly when it keeps its place and type.

**Model** (`ret_image`, `_result_broken`). A result is intact iff every piece
the old caller reads sits in the same register bits, as the same kind of
value, in the new result -- a struct's eightbytes take `rax`, `rdx` or `xmm0`,
`xmm1` in order -- and, for a result in caller memory, the new struct is no
larger than the old caller's buffer. A scalar is never the same value as a
struct member. Members the old caller does not read do not matter.

**Oracle.** `formal/experiments/struct_ret_fragment.py`: a caller built
against the old header calls the new library in a forked child, each call in
its own frame under `-fstack-protector-all` (a new function writing past the
old buffer trips the canary; AddressSanitizer does not see it, because gcc
leaves the sret store uninstrumented). Intact iff the child exits cleanly and
every member it reads holds the marker of the new member of the same name.

**Result.** 1190/1190 agreement for gcc- and clang-built libraries, dumped
through both header backends (castxml, clang), with no UNKNOWN; 12 pairs are
intact, the same 12 on both compilers -- a struct growing within its register
class or into a second register (`{int}` to `{int, long}`), never a memory
result. The observed set is committed as `formal/struct_ret_fragment_observed.json`
and every pair is re-checked in the unit lane
(`tests/test_call_interface_struct_returns.py`); the integration lane re-runs
the execution for gcc (`tests/test_call_interface_struct_returns_integration.py`).

**Proved** (`formal/lean/AbiLean/StructRetFragment.lean`). The structs, their
natural layout (byte offsets) and the psABI classification are defined in
Lean; the oracle (`intact`) reads each old member from where the *old*
classification puts it (the `k`-th register of the struct's class for
eightbyte `k`, and the byte in it, or the caller's buffer) and asks that the
new function leave the member of the same type at the same place, and that a
new function returning in memory be called with a buffer at least as large.
abicheck's comparison (`checkIntact`: no `ret_image` piece lost, memory not
grown) is modelled separately. For **any** nonempty member list (no
enumeration):

* `checkIntact_iff_intact` -- abicheck's image comparison is the oracle;
* `intact_iff_sublayout` -- intact iff every old member (type and offset) is a
  member of the new struct, both or neither come back in memory, and a memory
  result did not grow. The register shape need not be the same (`{int}` to
  `{int, long}` is INT1 to INT2, and intact);
* `reg_member_cls` -- every member of a register-returned struct has the
  struct's class (a `double` fills its eightbyte alone), which is why the
  register of a kept member cannot change.

On the fragment: `mem_structs` (35 structs), `mem_space`, `space_nodup`,
`space_length` (1190), the generator bijection (`decode_gen`, `gen_decode`,
`generated_in_exactly_one`), and `intact_pairs`/`check_pairs`: the oracle and
abicheck pick out the same 12 pairs, which are the pairs execution observed.
`StructRetFragmentExport.lean` writes `lean/struct_ret_fragment_cases.json`
(structs, layouts, shapes, every pair's verdict, an FNV-1a-64 checksum of the
generator); `tests/test_formal_struct_ret_fragment.py` checks it against the
experiment's own classifier and space, against
`struct_ret_fragment_observed.json`, and against `assess_call_interface` on
every pair. Assumed, not proved: that a member read returns the stored value
iff the member of that type was stored at that place (execution checks it),
and the psABI rules themselves.

**Outside the domain** (`None`, the axis reads UNKNOWN): unions, mixed
INTEGER/SSE eightbytes, `float`, `long double` and vector members, arrays of
fundamental types (the element's ABI fact is not stored), enum fields with no
ABI fact (the clang backend records none), base classes, packed or
over-aligned records, and a C++ record not proved trivially copyable (Itanium
returns a record that is non-trivial for calls in memory; the register rules
are applied only to a C parse or a proved-trivial record). Struct parameters
are not modelled.

## What is and is not proved

| Claim | Status |
|---|---|
| Enumeration = space, 1176 cases; generator bijective | **proved** (Lean) |
| When the arguments and the call interface survive, for lists of any length | **proved** for the model |
| The model is what gcc and clang do | **tested** by execution on every case, both compilers, x86-64 |
| abicheck meets the report contract | **tested** on every case, both builds |
| The SysV return rules themselves | **assumed** from the psABI (§3.2.3): classification of the three structs, `rdi` for the hidden pointer |
| Same observable ⇔ same key; reduced set covers every class | **proved** / **tested** |
| Same-class struct returns compared by member layout (E14b) | **proved** for the model, for any member list (`StructRetFragment.lean`); the 1190-case table equals execution on both compilers and abicheck on every pair (**tested**) |

## Reproduce

```bash
(cd formal/lean && lake build && lake env lean AbiLean/RetFragmentExport.lean && lake env lean AbiLean/StructRetFragmentExport.lean)
pytest tests/test_formal_struct_ret_fragment.py -q                # E14b: Lean = execution = abicheck
pytest tests/test_formal_ret_fragment.py -q                       # fast checks
pytest tests/test_formal_ret_fragment.py -q -m integration        # + execution, abicheck (add slow: full set)
python formal/experiments/ret_fragment.py OUT [--reduced]
```

Every theorem uses at most `propext`, `Classical.choice` and `Quot.sound`.
`breaking_but_call_compatible` and `scalar_to_sret_moves_args` use none.
There is no `sorry` and no `native_decide`.
