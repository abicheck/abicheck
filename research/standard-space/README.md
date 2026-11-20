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

## Work register

| Priority | Profile | Next experiment and exit criterion |
| --- | --- | --- |
| 1 | C99 shared subset | Enumerate argument → return, hidden result, va_arg and pointer access separately. Confirm each location or offset with an independent client; include a compatible and a divergent old/new pair. |
| 2 | C11 / C17 / C23 | Add C11 _Alignas and _Atomic, C17 controls and C23 fixed-underlying enum and _BitInt where supported. Record unsupported frontend features. Measure width, alignment and call class on supported compilers. |
| 3 | C++98 / 03 | Freeze class layout, virtual base, secondary dispatch, VTT during construction, RTTI and EH fixtures. Isolate each observation even when one mutation changes multiple facts. |
| 4 | C++11 / 14 | Contrast trivial and nontrivial copy/destruction for by-value calls, alignment and padding. Record old caller and new callee locations; capture a compatible control for each. |
| 5 | C++17 / 20 / 23 | Add reverse directions to both name changes, noexcept on top-level functions as a control, no_unique_address layout and C++23 feature-specific fixtures. Determine whether each needs a new observable or only a new trigger. |
| 6 | abiforge / abicheck | For each row map spec clause → bounded domain → operator → generated artifact → independent observation → diagnostic per evidence mode. Track missing, unsupported, ambiguous, false positive and false negative separately. Repeat on Clang and mixed compiler pairs. |

The earlier budget of 38 review rows and 164 planned tests was a manual allocation. It has no mathematical relationship to the measured layout count of 38. Report coverage separately for each bounded domain and operation. Establish a reduction rule before extrapolating to more fields, nested types or other ABIs.

Sources: [WG14 N1256](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1256.pdf), [AMD64 psABI](https://gitlab.com/x86-psABIs/x86-64-ABI), [Itanium C++ ABI](https://itanium-cxx-abi.github.io/cxx-abi/abi.html), [WG21 P0012R1](https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2016/p0012r1.html), [WG21 P0482R6](https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2018/p0482r6.html).
