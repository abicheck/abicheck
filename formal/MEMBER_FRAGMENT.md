# The implicit-`this` extension of E13/E14 (E15)

**Question.** Does the call model the E13 and E14 fragments proved for C
functions also decide a non-static C++ member function, whose implicit `this`
is not among the declared parameters?

**Answer.** Yes on x86-64 System V with the Itanium C++ ABI, without a new
model: `this` is a prepended pointer argument, and the proved placement already
covers it. Confirmed by execution with g++ and clang++ on every E13 and E14
case re-generated as a member, and on 170 static/non-static transitions.

## The claim

A non-static member `R K::f(P1, ..., Pn)` is called exactly like the free
function `R f(K *this, P1, ..., Pn)`:

* `this` is an object pointer, INTEGER class, so it takes the first INTEGER
  register (`rdi`);
* when `R` is returned in memory, the hidden sret pointer comes **first**
  (`rdi`) and `this` second (`rsi`). Checked in the assembly g++ and clang++
  emit at `-O0` and `-O2` for `struct S3 { long a, b, c; }; S3 A::f(long)`:
  both read the sret address from `rdi`, `this` from `rsi` and `p` from `rdx`.
  (Microsoft x64 does the opposite; it is not modelled.)

That is `FuncFragment.locAt` / `RetFragment.argLoc` applied to the parameter
list `ptr :: P` (`withThis`); `this_loc` / `this_after_sret` state where `this`
lands. `formal/lean/AbiLean/MemberFragment.lean` proves, with no `sorry` and
only Lean's standard axioms:

* **The shift lemma.** `locAt_this` / `argLoc_this`: declared argument `k` of a
  member is at its free-function location mapped by `shiftLoc` (INTEGER index
  plus one, SSE unchanged), which is injective. Hence, for **every** parameter
  list and every E13 edit, `member_call_compatible : callCompatible (member c)
  = callCompatible c` (the edit moved past `this`); and for E14,
  `member_ret_call_compatible` (same answer) and `member_argLoc_eq_iff` (each
  declared argument is placed alike iff it was in the free function, whatever
  the sret statuses). `this` itself moves (`rdi` <-> `rsi`) exactly when the
  sret status changes (`this_intact_iff`), so `member_args_intact_iff`: a
  member's arguments survive a return change iff the sret status is unchanged
  (a free function with only `double` parameters survives it anyway). The
  model places no register limit, so these hold for any length; register
  exhaustion is outside the model (E13/E14 use at most 4 INTEGER registers).
* **Static <-> non-static.** `transition` is E13's `ins 0 void*` / `rem 0`.
  `static_to_instance_incompatible`: never compatible.
  `instance_to_static_compatible_iff`: compatible iff every declared parameter
  is SSE (`double`); the stray `this` in `rdi` is ignored.
  `compatible_transitions` lists the 4 compatible transitions among the 170,
  and `MemberFragmentExport.lean` writes the table
  (`lean/member_fragment_cases.json`), which
  `tests/test_formal_member_fragment.py` compares case by case with
  `member_fragment_observed.json`.

## Execution oracle

`experiments/member_fragment.py OUT_DIR [--write-observed]` generates, for each
case, `struct K { long tag[3]; ... }` with the old and new member declaration,
gives both sides the same asm label (a C++ symbol mangles the parameter types;
this oracle asks what a changed signature behind an unchanged symbol does,
which is the `call` axis, not the `load` axis), and runs each call of the old
caller against the new library in its own forked child. The new member records
`this` and its arguments in shared memory; the old caller checks the result,
that `k` and a sentinel buffer were not written through, and that the child did
not crash.

| Set | Cases | g++ | clang++ |
|---|---|---|---|
| E13 cases as members (`instance -> instance`) | 2160 | 2160 = Lean | 2160 = Lean |
| E14 cases as members (`instance -> instance`) | 1176 | 1176 = Lean | 1176 = Lean |
| `static -> instance`, 85 parameter lists | 85 | 0 compatible | 0 compatible |
| `instance -> static`, 85 parameter lists | 85 | 4 compatible | 4 compatible |

The four compatible `instance -> static` cases are the lists with no INTEGER
parameter (empty, and one to three `double`s): the stray `this` in `rdi` is
ignored. The 170 transition observations are committed as
`member_fragment_observed.json` with a SHA-256 of their rows.

## Production

`abicheck/compare/call_interface.py` prepends the slot `this` (`I64`) for a
function whose `Function.member_kind_fact` is `instance`, from the header
backends' AST node kind (`abicheck/extract/headers/member_kind.py`).
`tests/test_call_interface_members.py` checks it against every E13 and E14
case as a member (Lean's answer) and every committed transition observation.
Outside the claim, and so `UNKNOWN`: constructors and destructors (C1/C2 and
D0/D1/D2 variants; the base-object variant of a class with virtual bases takes
a VTT argument after `this`), and a function whose member kind is not recorded
(DWARF-only evidence, the GCC plugin, a snapshot from before extractor generation 85).
