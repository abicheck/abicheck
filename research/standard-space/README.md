# How large is a C or C++ ABI validation campaign?

This experiment studies replacement of a native shared library for an already compiled client on Linux x86-64 SysV LP64. It counts three different objects: declarations, machine observations and old/new transitions. Only the last directly tests compatibility. A generator operator can exercise several transitions. Multiple operators can exercise the same one.

## Reproduce

Run `python3 research/standard-space/probe.py` on x86-64 Linux with Python 3.11+, GCC/G++ 14 and GNU nm. The script builds in a temporary directory and regenerates the three CSV files beside it. Use the GCC driver spellings c23 and c++23. These measurements describe the implementation in GCC 14.2.0, including its level of support for each dialect.

## C99 baseline and the four C dialects

In WG14 N1256 §6.2.5 one useful grammar has 18 basic choices (12 integer/character and six real/complex), void, enum, and five constructors (pointer, array, struct, union and function). This is a counting convention. Qualifiers, implementation-defined extensions and optional imaginary types are outside this grammar. Constructors can nest, so 25 entries do not bound the number of interfaces.

Enumerating one or two ordered fields over the 18 basic types gives 18 + 324 structs and as many unions. Four strata (one-field struct, two-field struct, one-field union, two-field union) yield 8, 14, 8 and 8 distinct tuples respectively: size, embedding offset after a char, and field offsets. The sum 38 retains the stratum label. All 684 tuples matched their C99 baseline under C11, C17 and C23 (2,736 measurements across four modes). This is the shared C99 subset. A tuple omits field width, bit layout, value encoding and calling convention.

For the argument transport experiment each field is long (I) or double (S). Two ordered fields, 0–6 leading integer arguments and 0–8 leading double arguments give 252 cases. An assembly callee records the six integer slots, eight SSE slots and two incoming stack words. The observer verifies both markers at the locations predicted by the psABI. There were 45 II, 48 IS, 48 SI, 49 SS and 62 stack placements. Register exhaustion causes the whole argument to fall back to the stack. These five outcomes merge physical register numbers and causes of exhaustion. Retain boundary cases to exercise different paths. These are placement observations, without changing a library or evaluating abicheck.

## C++98 to C++23

Build a client and DSO in the old mode, verify baseline success, rebuild only the DSO and rerun the original executable. Record defined dynamic symbols from nm and the runtime outcome in cpp.csv.

| Fixture | Old → new | Observed |
| --- | --- | --- |
| take(Pair), Pair contains long and double | Every ordered pair from 98, 03, 11, 14, 17, 20, 23 | All 49 clients returned the expected result |
| Callback parameter declared with noexcept | 14 → 17 | Old mangled name missing. The exception specification participates in the callback type |
| Parameter declared with decltype(u8"x"[0]) | 17 → 20 | Old mangled name missing. The literal element changes from char to char8_t, changing the reference type |

The latter two experiments use identical source text for both providers. Both break through the same external-symbol lookup mechanism. They need distinct generator inputs. The 49 control runs are one interface under 49 configurations. They establish neither general interoperability for those revisions nor coverage of the C++ object model. Standard-library ABI settings, compiler versions and ABI-version flags were held fixed.

## Coverage against the P0 inventory

The [P0 inventory](../../docs/contribute/plans/abiforge-abi-inventory.md) owns the 40 local families and 12 interactions. Its 208 fixture-role slots are a planning budget. The older [38-row campaign](../coverage-audit/campaign.tsv) is historical. Use the [reduction research plan](../../docs/contribute/plans/abiforge-space-reduction-research.md) for acceptance criteria.

| P0 obligation | Evidence here | Remaining boundary |
| --- | --- | --- |
| L2, field order | [Frozen clients](operations.csv) observe reordered fields through pointers, returns and varargs. [Generated clients](generated_clients.csv) reproduce field swaps. | The swaps preserve transport classes; no credit for the L2 × C1 interaction that changes classification. |
| C1 × C3, aggregate argument under register pressure | [252 placements](calls.csv) checked at assembly entry for four shapes. | Five transport outcomes merge register indices and exhaustion causes. Validate any proposed CI reduction against checker regressions. |
| C2 and C4, return and variadic access | [Five shapes](operations.csv), including a three-long result; 54 variadic contexts per shape. | Values verified; hidden-result location and va_list cursor transitions still need independent checks. |
| H1, H4 and H1 × V2, secondary-base adjustment | Old client and provider disagree on the base address; [checker findings](checker.json) include a changed adjustment thunk. | Extend the single hierarchy and compiler before extrapolating. |
| V1 and V4, normal and construction dispatch | Old-client slot change is observable in both phases. | No isolated construction-table mutation; keep V4 open. |
| M1, mangled names | [C++ dialect pairs](cpp.csv) isolate two unresolved old symbols. | Reverse directions, mixed compilers and additional triggers remain open. |

### Shared runner and entity attribution

Join the four generated pairs by seed and operator with the [existing sweep](../../abiforge/receipts/competitors/sweep/pairs.csv). Its dwarf+headers and stripped+headers lanes complement the two header-free modes in checker.json. These are separate builds. Keep evidence and compiler metadata when comparing verdicts.

For cxx_classes.virtual_reorder, both header-aware [raw receipts](../../abiforge/receipts/competitors/sweep/pairs.jsonl.gz) expect entities area and kind from the header-text diff. The checker emits type_vtable_changed on Shape. This explains decision_outcome=correct together with detail_entity=miss: the record is identified, while the individual affected methods are absent from that finding. Retain the historical score. Evaluate owner and affected members separately in a future runner change; include a wrong-owner control.

The existing [derive consumer](../../abiforge/run/consumers.py) exercises both dispatch directions on client-created objects. The research fixture adds a provider-owned object and separately observes the library call (70 → 70) and the frozen client's direct call (7 → 1). Reuse derive and the existing judge when promoting assertions. Keep these observations distinct.

### Next measurements

1. Check method attribution and provider ownership using the shared runner; require separate expectations for owner, members and both dispatch observations.
2. Extend C2/C4 with physical-location checks and V4 with a construction-only mutation and a compatible control. Record unsupported observations explicitly.
3. Extend the P0 rows with C11 alignment and atomics, supported C23 types, nontrivial C++ parameters, RTTI/EH, and Clang/mixed builds. Record time, exclusions and independently witnessed transitions per profile.

Sources: [WG14 N1256](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1256.pdf), [AMD64 psABI](https://gitlab.com/x86-psABIs/x86-64-ABI), [Itanium C++ ABI](https://itanium-cxx-abi.github.io/cxx-abi/abi.html), [WG21 P0012R1](https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2016/p0012r1.html), [WG21 P0482R6](https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2018/p0482r6.html).

## Old-client results for three aggregate operations and C++ subobjects

Run `python3 research/standard-space/operations.py` on the same GCC/G++ 14.2 Linux AMD64 profile. The 280 C99 controls exercise five shapes (II, IS, SI, SS, III). For each shape there is one return, one pointer read and 54 variadic extractions. The variadic client has one fixed integer marker, then 0–5 integer and 0–8 floating variadic prefix arguments. Both baseline and same-source replacement were run. The observer checks field values, including a three-long result above the two-eightbyte register threshold. It does not inspect return registers or va_list cursor locations directly.

Swapping the first two named long fields in two- and three-long records changed the old client's result from 11 to 22 in all six operation/shape pairs. Sizes and field transport classes were held constant. For these mutations a register classifier alone cannot establish compatibility.

For both C++98 and C++03, growing the primary base made the old client's secondary-base address differ from the provider's address. Calls using the provider's correct base pointer still returned 33 after construction and 11 during base construction. Swapping virtual slots instead preserved the base address but changed these results to 44 and 22 respectively. The client callback runs inside a virtual-base hierarchy. A VTT-only mutation remains open. See operations.csv for both stages of each experiment. These fixtures measure runtime observations independently of the generator and checker.

## Checker and generator calibration

Run checker.py and generated_clients.py with PYTHONPATH pointing to the checkout and abicheck dependencies installed. Measurements used checker 691862f and GCC/G++ 14.2, with no headers supplied to the checker. checker.json contains verdict, assurance, exit code and diagnostic kind/symbol for each comparison. Four generated operator/seed pairs complement six changed research fixtures and one identical-source control. Among the ten changed pairs, DWARF yielded ten BREAKING verdicts; stripped ELF yielded three BREAKING (adjustment thunk changed) and seven UNCONFIRMED. The control yielded NO_CHANGE with DWARF and UNCONFIRMED stripped. Several pairs repeat the same mechanism.

generated_clients.csv records four old-client checks against abiforge provider replacements. For virtual_reorder, each cell records provider-side dispatch and direct client-side dispatch on the same provider-owned object. Only the direct call changes. Small observer exports for this case and primary_base_grew enable cross-DSO observation; checker.json uses the original generator render without these adapters. This establishes mutation and detection coverage for three mechanisms, without certifying all contexts or a construction-only VTT change.

## Follow-up results and implementation priorities

| Experiment | Decision supported by the measurement |
| --- | --- |
| [Checker calibration](followup/matrix/README.md) | Assert owner and individual method attribution independently. For the same unstripped virtual_reorder pair, supplying headers loses both method names. Three edited controls produce zero false BREAKING verdicts across four evidence modes. |
| [Fault retention](followup/reduction/README.md) | Keep a dense reference run. Nine prefix representatives miss a reconstructed negative-capacity fault; the full grid detects it. Both array suites detect the historical loss of DWARF bounds in #1962. |
| [Construction dispatch and cross-DSO runtime](followup/cpp/README.md) | Add a focused regression for changes to a VTT entry's relocation target while VTT size stays fixed. The GCC-built DWARF pair gets NO_CHANGE despite the frozen client's changed construction-only result. Establish stable relocation comparison under layout-preserving relinks before introducing a breaking diagnostic. |
| [oneDAL 2025.10.0](followup/onedal/README.md) | Sample polymorphic interfaces and by-value returns, and improve binding for templates and special members before using signature counts to rank the whole product. The 369 exact linker-name joins cover 11.2% of the defined function exports in this DSO. |

The VTT and method-attribution cases are separate checker regressions to guard: one affects the verdict, the other the specificity of a correct verdict. CI selection should track which known faults survive a reduction alongside ABI-obligation coverage.
