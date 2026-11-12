# A closed layout fragment: completeness relative to an explicit space

This is the one layout fragment for which the whole chain is closed:
an explicit case space, a generator proved to cover it bijectively, an
oracle for every case, a compiler and abicheck run over every case, and a
reduced set whose equivalence condition is proved. It answers the review
point that the earlier layout results (E3/E4: offset and size theorems, the
exported 1248-case table, compiler probes) showed properties of the layout
model, not that the generator was complete.

**The claim, exactly.** For the space **F** defined below: every case of F
has exactly one generated program pair and every generated pair is in exactly
one case of F (proved); every case's expected result follows from the Lean
layout model (computed); gcc 13.3 and clang 18.1 lay out every case as the
model does, and abicheck reports exactly the expected findings and verdict
for every case on both builds (tested exhaustively, 0 mismatches); two cases
have the same observable result iff they have the same class key (proved),
and the 32-case reduced set contains one case of each of the 32 classes
(tested). Nothing is claimed outside F.

Files: [`lean/AbiLean/Fragment.lean`](lean/AbiLean/Fragment.lean) (space,
generator, oracle, theorems), [`lean/AbiLean/FragmentExport.lean`](lean/AbiLean/FragmentExport.lean)
→ [`lean/fragment_cases.json`](lean/fragment_cases.json),
[`experiments/layout_fragment.py`](experiments/layout_fragment.py) (C
generator, compiler and abicheck run),
[`../tests/test_formal_layout_fragment.py`](../tests/test_formal_layout_fragment.py).

## The fragment F

| Axis | In F | Outside F |
|---|---|---|
| Language, ABI | C11, LP64 System V (x86-64; AArch64 AAPCS64 has the same rules for these types) | ILP32 (i386: `long`/`double` 4-aligned in structs), Windows LLP64, C++ |
| Field types | scalar size classes with size = alignment ∈ {1, 2, 4, 8}, spelled `char`, `short`, `int`, `long` | other spellings of the same class (`float`, `double`, pointers, `long long`): same layout by the ABI, **not generated**; 16-byte types; arrays, nested records, enums, bit-fields |
| Old struct | 1–4 named fields `f0 … f(n-1)` | empty structs, more than 4 fields, unions, `packed`/`aligned`/`#pragma pack`, flexible array members |
| Edit | insert one field `ins` at position 0 … n (n = append) | removal, reorder, type change, several edits, rename |
| Exposure | record passed by pointer to an exported function (`int use_sN(struct sN *)`), headers given | by-value passing (register classification), opaque records, inline functions |
| Evidence | both sides compiled with `-g -O0`, public header supplied (castxml) | DWARF-only or header-only, stripped binaries, unknown evidence (E9) |
| Observable | the set of findings (ChangeKind and member) and the verdict per case | numeric old/new values in findings (checked as layout, not as the observable), `affected_symbols`, report wording, other output formats |

Size: |F| = Σ_{n=1..4} 4ⁿ · (n+1) · 4 = **6368** cases (`space_length`).

## The five parts

### 1. The space, defined without the generator

`Case = (base : List Sc, pos : Nat, ins : Sc)` and
`InSpace c := 1 ≤ |base| ≤ 4 ∧ pos ≤ |base|`. `space` enumerates words over
`Sc` by length. Proved:

| Theorem | Statement |
|---|---|
| `mem_space` | `c ∈ space ↔ InSpace c` (the enumeration is complete and sound) |
| `space_nodup` | no case is listed twice |
| `space_length` | `space.length = 6368` |

### 2. The generator and its inverse

Abstractly, `gen : Case → Program` maps a case to the old and new field
declaration lists; `decode : Program → Option Case` reads one back. Proved:

| Theorem | Statement |
|---|---|
| `decode_gen` | `InSpace c → decode (gen c) = some c` |
| `gen_decode` | `decode p = some c → InSpace c ∧ gen c = p` |
| `gen_injective` | distinct cases give distinct programs |
| `every_case_generated` | every case of the space has a generated program |
| `generated_in_exactly_one` | every generated program is `gen c` for exactly one case `c` of the space |

The C generator (`layout_fragment.generate`) is a separate realization with
its own enumeration order. `test_generator_is_a_bijection_onto_the_space`
decodes each of its programs from the C text and checks the result is a
bijection onto the space predicate, enumerated again in the test with
`itertools`; `test_generator_realizes_leans_gen` checks that its text for
every case equals the rendering of Lean's `gen`. Field names are part of a
case: `{char f0}` with `char ins` at 0 and at 1 give the same type list but
different cases (in one `f0` moves, in the other it does not).

### 3. The oracle

From `AbiLean.Layout` (`offset_i = alignUp(end_{i-1}, align_i)`,
`sizeof = alignUp(end, maxAlign)`): old/new offsets, sizeof and alignment per
case, then the expected findings:

* `type_field_added_compatible` on `ins` — always (compatible);
* `type_field_offset_changed` on each old member whose offset changed
  (breaking; justified by `AbiLean.fires_sound`, README.md);
* `type_size_changed` on the record if sizeof changed (breaking, catalog default);
* `type_alignment_changed` on the record if the alignment changed (breaking, catalog default);

verdict breaking iff anything but the addition is reported. These rules are
the product contract for this fragment, written down once; they are not
copied from abicheck's output. `test_oracle_layout_matches_reference_layout_rule`
re-derives every exported layout with an independent implementation in the
test, and `test_class_findings_follow_from_the_key` re-derives the findings.

### 4. Compiler and abicheck run (every case)

`layout_fragment.py` builds all cases as one library pair per compiler
(`-std=c11 -g -O0 -shared -fPIC`), asserts the scalar premise with
`_Static_assert(sizeof(T) == n && _Alignof(T) == n)`, probes `sizeof`,
`_Alignof` and every `offsetof`, and runs `abicheck compare` with headers.

```
                          gcc 13.3.0        clang 18.1.3
layout = Lean oracle      12736 / 12736     12736 / 12736    (old and new side of 6368 cases)
abicheck = oracle          6368 /  6368      6368 /  6368    (findings + verdict per case)
```

Per-case mode (each case its own library pair and `compare` run, so the exit
code is the case's own verdict: 4 for breaking, 0 for compatible) is reported
under [Cost](#cost).

### 5. Reduction with a proved equivalence condition

`key c = (moved old fields, sizeof changed, alignment changed)`.

| Theorem | Statement |
|---|---|
| `prefix_not_moved` | fields before the insertion point never move |
| `moved_contiguous` | from the insertion point on, once a field does not move, no later field moves |
| `movedFields_interval` | the moved fields are exactly the interval `[pos, pos + L)` |
| `obs_eq_iff_key` | `obs c₁ = obs c₂ ↔ key c₁ = key c₂` — **two cases have the same observable result iff they have the same key** |
| `opName_not_enough`, `append_not_enough` | the operation (struct length, position, inserted size) does not decide the class: `{char; int}` and `{int; int}` with `char` inserted at 1 differ (compatible vs breaking); same for append into padding |

By `movedFields_interval` the key is determined by four small values, so at
most 11 · 2 · 2 = 44 keys exist for n ≤ 4; **32** occur. The reduced set is
the first case of `space` with each key (exported as `rep`).
`test_reduced_set_covers_every_class_exactly_once` checks it hits all 32
classes once; `test_classes_partition_the_space_by_the_reference_key` checks
the classes against keys recomputed independently. Merging by operation
would be unsound for most operations: the test finds more than half of the
(length, position, inserted size) groups span several classes.

## Cost

Measured on one 4-core container (Python 3.13, castxml 0.7.0), both
compilers, wall time:

| Set | Cases | Batch (one library pair per compiler) | Per case (own build + compare, 4 jobs) |
|---|---|---|---|
| Full F | 6368 | 73 s | ≈ 2.2 h (estimate from the reduced run, 1.2 s per case; full measurement pending) |
| Reduced | 32 | 4.5 s | 39 s |
| Ratio | **199×** | 16× | ≈ 200× |

The batch ratio is lower than the case ratio because process start-up and
the castxml parse dominate small batches. Generating and proving costs:
`lake build` of `Fragment.lean` ≈ 5 s; the export ≈ 25 s; the fast tests
(no compiler) ≈ 2 s.

## What is and is not proved

| Claim | Status |
|---|---|
| The enumeration is exactly the space, without duplicates, 6368 cases | **proved** (Lean) |
| The abstract generator is a bijection between the space and its image | **proved** (Lean) |
| The C generator realizes the abstract one, and is a bijection onto the space | **tested** exhaustively (6368 programs, text compared with Lean's `gen`) |
| Oracle findings/verdict per case from the layout model | **computed** in Lean; re-derived by an independent implementation in the tests |
| Same observable result ⇔ same key; the moved fields form an interval | **proved** (Lean), for the model |
| The reduced set contains every class once | **tested** exhaustively |
| Compilers lay out every case as the model | **tested** exhaustively, gcc 13.3 and clang 18.1, x86-64 |
| abicheck reports the oracle's findings and verdict for every case | **tested** exhaustively, both builds |
| abicheck's result on a non-representative equals its result on the representative | **assumed** for the reduced set alone. It holds in this version (the full run found 0 mismatches), but the theorem is about the model; a defect depending on the numeric values (for example only large offset deltas) could pass the reduced set. Run the full set to establish the claim; the reduced set is a cheap regression check over every class |
| The model's layout rule is the ABI | **assumed** from the SysV psABI; observed on two compilers on one host |
| Results transfer to other spellings of a size class, other compilers/versions, AArch64 | **not tested** |

### A side observation outside the observable

`affected_symbols` is not part of the observable, and it is wrong in this
corpus: the change to `struct s1` lists `use_s10 … use_s199` as affected,
because the enrichment matches type names as substrings
(`diff_filtering._build_type_to_funcs` / `_SubstringMatcher`). Recorded in
`docs/contribute/known-gaps.md`; not fixed here.

## Reproduce

```bash
(cd formal/lean && lake build && lake env lean AbiLean/FragmentExport.lean)  # proofs + export
pytest tests/test_formal_layout_fragment.py -q                               # fast checks
pytest tests/test_formal_layout_fragment.py -q -m integration                # + compilers, abicheck (add slow: full set)
python formal/experiments/layout_fragment.py OUT [--reduced] [--per-case]    # cost measurements
```

`#print axioms` (end of `FragmentExport.lean`): every theorem above uses at
most `propext`, `Classical.choice`, `Quot.sound`; `opName_not_enough` uses
none. No `sorry`, no `native_decide`.
