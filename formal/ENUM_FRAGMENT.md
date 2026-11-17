# A closed enum fragment: completeness relative to an explicit space

The second fragment closed end to end (E12), after the layout fragment
([`FRAGMENT.md`](FRAGMENT.md), E10). It has the same five parts: an explicit
case space, a generator proved to cover it bijectively, an oracle for every
case, a compiler and abicheck run over every case, and a reduced set whose
equivalence condition is proved.

**The claim, exactly.** For the space **E** defined below:
- every case of E has exactly one generated program pair, and every generated
  pair is in exactly one case of E (proved);
- every case's expected result follows from C11's enumerator value rule and
  the product contract below (computed);
- gcc 13.3 and clang 18.1 give every enumerator the model's value and every
  enum 4 bytes, and abicheck reports exactly the expected findings and
  verdict for every case on both builds (tested exhaustively, 0 mismatches);
- two cases have the same observable result iff they have the same class key
  (proved), and the 32-case reduced set contains one case of each of the 32
  classes (tested).

Nothing is claimed outside E.

Files:
- [`lean/AbiLean/EnumFragment.lean`](lean/AbiLean/EnumFragment.lean): the
  space, the generator, the oracle and the theorems.
- [`lean/AbiLean/EnumFragmentExport.lean`](lean/AbiLean/EnumFragmentExport.lean),
  which writes [`lean/enum_fragment_cases.json`](lean/enum_fragment_cases.json):
  one row per case (initializers, edit, old and new values, class), one row
  per class (key, findings, verdict, representative), and the `gen` checksum.
- [`experiments/enum_fragment.py`](experiments/enum_fragment.py): the C
  generator, and the compiler and abicheck run.
- [`../tests/test_formal_enum_fragment.py`](../tests/test_formal_enum_fragment.py).

## The fragment E

| Axis | In E | Outside E |
|---|---|---|
| Language, ABI | C11 (`-std=c11 -pedantic-errors`), LP64 System V | C++ (`enum class`, fixed underlying types), C23 fixed types, ILP32, Windows |
| Old enum | 1–4 enumerators `e0 … e(n-1)`, each with no initializer, `= 0`, `= 2` or `= -1` | other values (in particular ones that leave `int` and change the enum's size), more than 4 enumerators, constant expressions referring to other enumerators |
| Edit | insert one enumerator `ins` (same initializer domain) at position 0 … n; remove one (when n ≥ 2); change one enumerator's initializer to another of the domain | removing the only enumerator (an empty enumerator list is ill-formed, C11 6.7.2.2), rename, reorder, several edits, enum added/removed/made opaque |
| Exposure | the enum is the parameter type of an exported function (`int use_enN(enum enN)`), headers given | enum only in a struct field, opaque, unused |
| Evidence | both sides `-g -O0`, public header supplied (castxml) | DWARF-only or header-only, stripped binaries |
| Observable | the set of findings (ChangeKind and enumerator) and the verdict per case | the numeric old/new values in findings (checked as values, not as the observable), report wording, other formats |

Size: |E| = Σ_{n=1..4} 4ⁿ · (4(n+1) + [n ≥ 2]·n + 3n) = **11372** cases
(`space_length`).

C enumerators share one name space, so the C text prefixes each with its
enum's tag (`E<N>_e0`). The abstract generator and the checksum use the bare
names.

## The five parts

### 1. The space, defined without the generator

`Case = (base : List Init, op : Op)`, with `Init ∈ {imp, =0, =2, =-1}` and
`Op = ins p i | rem p | set p i`. `InSpace c := 1 ≤ |base| ≤ 4 ∧ OpOk base op`.
`OpOk` states the two exclusion rules: no removal from a one-enumerator enum,
and no `set` to the initializer an enumerator already has.

| Theorem | Statement |
|---|---|
| `mem_space` | `c ∈ space ↔ InSpace c` |
| `space_nodup` | no case is listed twice |
| `space_length` | `space.length = 11372` |

### 2. The generator and its inverse

`gen : Case → Program` maps a case to the old and new enumerator lists
(name and initializer per enumerator). `decode : Program → Option Case`
reads the edit back:
- an `ins` name gives an insertion;
- a list one shorter gives a removal at the first misnamed position;
- otherwise it is a `set` at the first differing position.

| Theorem | Statement |
|---|---|
| `decode_gen` | `InSpace c → decode (gen c) = some c` |
| `gen_decode` | `decode p = some c → InSpace c ∧ gen c = p` |
| `gen_injective`, `generated_in_exactly_one`, `every_case_generated` | as for the layout fragment |
| `gen_new_inits` | the oracle's new initializer list is the generated program's |

The C generator (`enum_fragment.generate`) has its own enumeration order and
two checks:
- `test_generator_is_a_bijection_onto_the_space` decodes each of its programs
  from the text and checks the result is a bijection onto the space predicate,
  which the test enumerates again with `itertools`.
- `test_generator_realizes_leans_gen` compares its text with Lean's `gen`. For
  all 11372 cases this is an FNV-1a-64 checksum; for the 32 representatives it
  is the full text.

### 3. The oracle

**Values (C11 6.7.2.2p3).** An enumerator with `= k` has value k. One without
an initializer has the previous enumerator's value plus 1, and the first one
has 0 (`values`).

**Expected findings.** These are the product contract for an enum used in an
exported signature:
- `enum_member_added` on `ins` (compatible: old code never sees the new value);
- `enum_member_removed` on the removed enumerator (breaking);
- `enum_member_value_changed` on every remaining old enumerator whose value
  changed (breaking: old binaries carry the old value).

The verdict is breaking iff a removal or a value change is reported,
compatible iff only the addition is, and `no_change` otherwise. A `no_change`
case is a respelling with the same value: `e0` → `e0 = 0`
(`respelling_is_no_change`).

`test_oracle_values_match_reference_value_rule` re-derives every exported
value with an independent implementation. `test_class_findings_follow_from_the_key`
re-derives the findings.

### 4. Compiler and abicheck run (every case)

`enum_fragment.py` builds all cases as one library pair per compiler. It
probes every enumerator's value and `sizeof(enum)`, then runs
`abicheck compare` with headers.

```
                          gcc 13.3.0        clang 18.1.3
values + size = oracle    22744 / 22744     22744 / 22744    (old and new side of 11372 cases)
abicheck = oracle         11372 / 11372     11372 / 11372    (findings + verdict per case)
findings on no fragment enum   0                 0
```

The reduced set was also run in per-case mode: each case gets its own build
and `compare`, so the exit code is the case's own verdict (0, 0, 4). All 32
cases agree on both compilers.

### 5. Reduction with a proved equivalence condition

`key c = (added, removed old enumerators, revalued old enumerators)`.

| Theorem | Statement |
|---|---|
| `prefix_unchanged` | old enumerators before the edit never change value |
| `changed_contiguous` | from the edit on, once an old enumerator keeps its value, no later one changes |
| `changed_interval` | the revalued old enumerators are exactly `[start, start + L)`, with `start` the edit position (the one after it for a removal) |
| `explicit_never_shifted` | an insertion or removal never revalues an enumerator with an explicit initializer: the shift runs only through implicit ones |
| `append_compatible` | appending an enumerator is never a break |
| `obs_eq_iff_key` | `obs c₁ = obs c₂ ↔ key c₁ = key c₂` |
| `op_not_enough` | the edit does not decide the class: inserting an implicit enumerator first is compatible before `e0 = 0` and breaking before an implicit `e0` |

**32** keys occur. The reduced set is the first case of `space` with each
key, 355× smaller than E.
`test_edit_alone_does_not_determine_the_class` finds that more than half of
the (length, edit, position, initializer) groups span several classes.

## The proofs' one idea

The proofs rest on one observation. From the edit position on, the old and
the new values of the old enumerators are the same initializer list
evaluated from two different starting values (`suffix_vals`). For an
initializer change at `p`, the list is `imp :: rest`, started one below each
side's value at `p`.

Each value depends only on its predecessor, so two evaluations that agree
once agree from then on (`valsFrom_sync`). An explicit initializer ignores
its predecessor (`valsFrom_explicit`). Contiguity, the interval, and the
explicit-enumerator theorem follow from these.

## Cost

Measured on one shared 4-core cloud container (Python 3.13, castxml 0.7.0):

| Set | Cases | Batch, both compilers | Per case, both compilers, 4 jobs |
|---|---|---|---|
| Full E | 11372 | 75 s | not run (≈ 3 h at the reduced set's rate) |
| Reduced | 32 | — | 32 s |

`lake build` of `EnumFragment.lean` takes ≈ 9 s and the export ≈ 6 s. The
fast tests (no compiler) take ≈ 12 s.

## What is and is not proved

| Claim | Status |
|---|---|
| The enumeration is exactly the space, without duplicates, 11372 cases | **proved** (Lean) |
| The abstract generator is a bijection between the space and its image | **proved** (Lean) |
| The C generator realizes the abstract one, and is a bijection onto the space | **tested** exhaustively |
| Oracle values and findings per case | **computed** in Lean; re-derived independently in the tests |
| Revalued enumerators form an interval; explicit ones are never shifted; same observable ⇔ same key | **proved** (Lean), for the model |
| The reduced set contains every class once | **tested** exhaustively |
| Compilers assign the model's values and a 4-byte enum | **tested** exhaustively, gcc 13.3 and clang 18.1, x86-64 |
| abicheck reports the oracle's findings and verdict for every case | **tested** exhaustively, both builds; per-case exit codes on the reduced set |
| abicheck's result on a non-representative equals its result on the representative | **assumed** for the reduced set alone (holds in this version: the full run found 0 mismatches) |
| The value rule is C11's | **assumed** from C11 6.7.2.2; observed on two compilers |

## Observations outside the observable

* **An underlying-type signedness change is not reported.** An `= -1`
  enumerator makes gcc and clang pick `int` instead of `unsigned int`; the
  size stays 4. Adding `ins = -1` to an enum of non-negative values is
  therefore `compatible`, with no finding about the type. The oracle agrees
  with that, for a stated reason: an old consumer never receives the new
  value except as a value it did not know, which the addition already
  reports. Whether a signedness change deserves its own finding (for example
  for an enum widened into a 64-bit integer by the consumer) is a product
  question, left open.
* **The enum's size never changes in E**, by the choice of the value domain.
  Values that leave `int` (C23, or a GCC extension under C11) make
  `enum_underlying_size_changed` reachable. That is the natural next
  extension of this fragment.

## Reproduce

```bash
(cd formal/lean && lake build && lake env lean AbiLean/EnumFragmentExport.lean)  # proofs + export
pytest tests/test_formal_enum_fragment.py -q                                     # fast checks
pytest tests/test_formal_enum_fragment.py -q -m integration                      # + compilers, abicheck (add slow: full set)
python formal/experiments/enum_fragment.py OUT [--reduced] [--per-case]          # runs and timings
```

`#print axioms` (end of `EnumFragmentExport.lean`): every theorem uses at
most `propext`, `Classical.choice` and `Quot.sound`. `op_not_enough` and
`respelling_is_no_change` use none. There is no `sorry` and no
`native_decide`.
