# oneDAL release profile

This snapshot uses Linux x86-64 wheels from PyPI for oneDAL 2025.10.0. The binary scope is one CPU library, libonedal.so.3. Reproduce from this directory with Python 3.11+, GCC system headers, GNU readelf and the x86-64 Linux libclang wheel.

```sh
python -m pip download --only-binary=:all: --no-deps -d wheels daal==2025.10.0 daal-include==2025.10.0 libclang==18.1.1
python profile.py wheels
```

The script checks all three wheel hashes. It parses the installed public umbrella oneapi/dal.hpp in C++17 mode and joins eligible function/method declarations to the binary's defined dynamic function symbols by exact linker name. All 3,287 exported names were counted, including aliases at a shared address. There were 369 exact joins (11.2%) and 2,918 unmatched names. Constructors, destructors, uninstantiated template patterns and many template specializations are outside this AST traversal. Unmatched names can include internal exports and public methods. The 11.2% measures extraction and matching yield on one DSO.

| Observed signature feature | All 369 joined exports | 47 public candidates |
| --- | ---: | ---: |
| Direct record parameter by value | 3 | 0 |
| Direct record return by value | 32 | 2 |
| Direct function pointer/reference callback parameter | 11 | 0 |
| Provably polymorphic boundary type, including implicit this | 40 | 17 |

The public-candidate filter excludes declarations whose header path contains a detail component or whose owner chain contains detail or internal. It is a convention, not a definitive ABI manifest. In particular, detail symbols can support client-compiled inline code. Each category is counted once per linker name and categories overlap. The two public record-return candidates are heterogen_table::empty and error_holder::get_actual. Counts of records describe C++ types; some are nontrivial and need a separate calling-convention probe before selecting an enum, register or hidden-result ABI case.

Client-side base conversion, dynamic call site, callback invocation and lifetimes are unknown without clients. The polymorphism column counts only visible virtual methods/destructors in a record or a recursively accessible base definition; absence of recognized evidence is inconclusive for incomplete or dependent types. Zero direct callbacks among the 47 candidates leaves type-erased, templated and indirect callbacks unclassified. This release provides a reason to sample concrete polymorphic and record-return interfaces, with template-aware symbol binding as the first extraction improvement. No checker verdict or production usage frequency is inferred.

Results: [result.json](result.json), [per-symbol joins](matches.csv).
