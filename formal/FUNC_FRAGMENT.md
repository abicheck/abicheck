# A closed function-signature fragment, with a calling-convention oracle

This is the third fragment closed end to end (E13), after record layout
([`FRAGMENT.md`](FRAGMENT.md), E10) and enums
([`ENUM_FRAGMENT.md`](ENUM_FRAGMENT.md), E12). Unlike those two, it has two
oracles:

* **The report contract.** This is what abicheck should report. Parameters are
  matched by position, because in C a parameter's identity is its position.
* **The call interface.** This asks whether an old binary still calls the new
  function correctly. The answer comes from a model of the x86-64 System V
  register assignment, and a real old caller calling the new library checks
  that model on every case.

The two disagree on 240 cases. The table under [Findings](#findings) gives the
breakdown.

**The claim, exactly.** For the space **S** defined below:

- every case of S has exactly one generated program pair, and every generated
  pair is in exactly one case of S (proved);
- the call interface survives an edit exactly when the theorems of part 4 say
  so, for parameter lists of any length (proved, for the model);
- gcc 13.3 and clang 18.1 behave as the model on every case (tested by
  execution, 0 mismatches);
- abicheck reports exactly the report contract's findings and verdict on every
  case for both builds (tested, 0 mismatches);
- two cases have the same observable result (findings, verdict, call
  compatibility) iff they have the same class key (proved). The 34-case
  reduced set contains one case of each class (tested).

**Files:**

- [`lean/AbiLean/FuncFragment.lean`](lean/AbiLean/FuncFragment.lean): the
  space, the generator, both oracles and the theorems.
- [`lean/AbiLean/FuncFragmentExport.lean`](lean/AbiLean/FuncFragmentExport.lean):
  writes [`lean/func_fragment_cases.json`](lean/func_fragment_cases.json).
- [`experiments/func_fragment.py`](experiments/func_fragment.py): the
  generator, the execution oracle and the abicheck run.
- [`../tests/test_formal_func_fragment.py`](../tests/test_formal_func_fragment.py).

## The fragment S

| Axis | In S | Outside S |
|---|---|---|
| Language, ABI | C11, x86-64 System V | C++ (overloading, mangling), AArch64, Windows x64, i386 |
| Old function | `int fN(…)` with 1–3 parameters `p0 … p(n-1)`, each `int`, `long`, `double` or `void *` | structs or unions by value, `float`, `char`/`short` (promotion and extension rules), `long double`, variadic functions, more than 6 INTEGER or 8 SSE arguments (stack passing) |
| Edit | insert one parameter `ins` at 0 … n; remove one; change one parameter's type | return type, several edits, reorder, rename only |
| Evidence | both sides `-g -O0`, public header supplied (castxml) | DWARF-only, header-only, stripped |
| Observable | abicheck's findings (kind and position) and verdict; whether an old caller still passes every surviving argument intact | report wording, numeric values in findings |

Size: |S| = Σ_{n=1..3} 4ⁿ · (4(n+1) + n + 3n) = **2160** cases.

## The five parts

### 1–2. Space and generator

These have the same shape as the enum fragment. Removing the only parameter
is allowed, and the function then becomes `f(void)`. Proved:

- `mem_space`
- `space_nodup`
- `space_length` (= 2160)
- `decode_gen`
- `gen_decode`
- `generated_in_exactly_one`
- `every_case_generated`

The C generator is checked against the space and against Lean's `gen`. The
`gen` check uses an FNV-1a-64 checksum over all cases, plus the full text for
each representative.

### 3. The two oracles

**Report contract** (`findings`, `verdict`). The report always contains
`func_params_changed`, whose catalog default is breaking. In addition, at
each position both lists have:

- `param_pointer_level_changed` where pointer-ness differs;
- otherwise `param_renamed` (risk) where the type is the same and the name is
  not.

Every case changes the type list, so every verdict is breaking
(`every_case_breaking`).

**Call interface** (`callCompatible`). SysV passes each scalar argument in
the next register of its class: INTEGER arguments in `rdi`, `rsi`, …, and SSE
arguments in `xmm0`, …. `locAt` states this without recursion: argument k's
register is numbered by how many earlier arguments share its class.

An old caller still calls the new function correctly iff both of these hold:

- nothing was inserted (an inserted parameter is read from a register the
  caller never set);
- every surviving old parameter is read from the register the caller wrote it
  to, in the same representation.

There are three representations: 32-bit integer, 64-bit integer (`long` and
`void *`), and 64-bit float.

**Execution oracle** (`func_fragment.py`). A caller is compiled against the
old header and linked against the new library. It passes a distinct marker
for every argument:

- a negative `int`, so that a zero-extended 32-bit write is visible to a
  64-bit read;
- a `long` and a pointer outside 32 bits;
- a `double`.

The new function records what it received. A case is call-compatible iff
every surviving argument arrived intact and nothing was inserted.

### 4. Theorems

| Theorem | Statement |
|---|---|
| `ins_never_compatible` | no insertion keeps the call interface |
| `set_compatible_iff` | changing one parameter's type keeps it **iff the representation is unchanged** (`long` ↔ `void *`). A class change moves later arguments of both classes, and a width change leaves the caller's upper bits undefined |
| `rem_compatible_iff` | removing a parameter keeps it **iff no later parameter has its class**. Later arguments of that class shift down one register; arguments of the other class stay put, and the caller's extra argument is ignored |
| `trailing_removal_compatible` | removing the last parameter always keeps it |
| `every_case_breaking` | the report contract calls every case breaking |
| `breaking_but_call_compatible` | `f(int, double)` → `f(double)` is breaking under the contract, and an old binary still calls it correctly |
| `obs_eq_iff_key` | same observable ⇔ same key (positional findings, call compatibility) |

`set_compatible_iff` and `rem_compatible_iff` are stated for any lists
`pre ++ x :: suf`, not only for lengths up to 3.

### 5. Runs

```
                               gcc 13.3.0      clang 18.1.3
execution = callCompatible     2160 / 2160     2160 / 2160     (240 call-compatible)
abicheck  = report contract    2160 / 2160     2160 / 2160
findings on no fragment function    0               0
```

The full space takes 16 s for both compilers. The 34 classes give a reduced
set 64× smaller.

## Findings

**abicheck calls 240 edits ABI breaks although every old binary keeps
working.** Those edits break source, so the accurate verdict is a source
break (`API_BREAK`, exit 2), not an ABI break (`BREAKING`, exit 4). They
are:

| Edit | Cases | Why old binaries still work | Why source breaks |
|---|---|---|---|
| Remove the last parameter | 84 | the caller's extra argument is ignored | a call with the extra argument no longer compiles |
| Remove a parameter with no later one of its class, e.g. the `int` in `f(int, double)` | 42 | the remaining arguments stay in their registers | same |
| `long` ↔ `void *` | 114 | same register, same 64-bit representation | an integer↔pointer conversion is a constraint violation in C |

abicheck reports all 240 as `breaking`, because `func_params_changed` is
breaking whatever the calling convention says. This fragment records that;
it does not change it. Changing it means making the verdict of a parameter
edit depend on the platform ABI, which is a product decision (a default and
an exit code). It is recorded in
[`docs/contribute/known-gaps.md`](../docs/contribute/known-gaps.md).

**Positional matching shows insertions as renames.** Inserting `int ins`
before `int p0` reports `param_renamed` at each later position whose type
did not change (`p0 → ins`, `p1 → p0`). That follows from positional
identity, which is right for C, and it is risk-level. It is part of the
contract here, not a finding.

## What is and is not proved

| Claim | Status |
|---|---|
| Enumeration = space, 2160 cases; generator bijective | **proved** (Lean) |
| When an edit keeps the call interface (`set_compatible_iff`, `rem_compatible_iff`, `ins_never_compatible`), for lists of any length | **proved** for the model |
| The model is what gcc and clang do | **tested** by execution on every case, both compilers, x86-64 |
| abicheck meets the report contract | **tested** on every case, both builds |
| The SysV register rule itself | **assumed** from the psABI (§3.2.3), for scalar `INTEGER`/`SSE` arguments that fit in registers |
| Same observable ⇔ same key; reduced set covers every class | **proved** / **tested** |

## Reproduce

```bash
(cd formal/lean && lake build && lake env lean AbiLean/FuncFragmentExport.lean)
pytest tests/test_formal_func_fragment.py -q                       # fast checks
pytest tests/test_formal_func_fragment.py -q -m integration        # + execution, abicheck (add slow: full set)
python formal/experiments/func_fragment.py OUT [--reduced]
```

Every theorem uses at most `propext`, `Classical.choice` and `Quot.sound`.
`breaking_but_call_compatible` uses none. There is no `sorry` and no
`native_decide`.
