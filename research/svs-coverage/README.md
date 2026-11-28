# SVS ABI scenario assessment on Linux x86-64

The inventory starts at the public C++ runtime headers and the C binding of
[Intel Scalable Vector Search at efa2d48](https://github.com/intel/ScalableVectorSearch/tree/efa2d48c9f88e2ec1afd024e5f9dd9ec9f33895f/bindings).
It groups declarations by what a frozen caller observes. Flat, IVF and Vamana
share several contracts. Python wheels, on-disk index formats, the header-only
template library and search accuracy are outside this measurement.

| SVS interface | General Forge witness | Measured outcome |
| --- | --- | --- |
| Virtual index methods and caller-provided filter/allocator implementations | L2 virtual insertion and reorder | BREAKING with type_vtable_changed. The compatible primary override returns COMPATIBLE. |
| C allocator, stream and threadpool operation tables | L2 callback_table | Swapped slots produce type_field_offset_changed. A changed argument passing convention produces value_abi_trait_changed. |
| Parameter/result records, Status returns, ResultsAllocator returning SearchResultsStorage | L2 layout, member_return and callback_return | Field swap and alignment changes are BREAKING. Return register class and indirect callback return changes are BREAKING. These are representative mutations of the contract family. |
| Inline Status operations calling private out-of-line code | Actual runtime api_defs.cpp compiled as a shared library | Hiding store_message while keeping headers fixed breaks the old client at symbol resolution (127). The ELF checker identifies func_visibility_changed (exit 4). |
| Caller-sized C input and output records | New sized_record client with a full and shortened logical extent | Swapping fields breaks the client and yields type_field_offset_changed. Ignoring the caller's extent reads an excluded value and overwrites its sentinel. The full-extent control still works. ELF/DWARF yields NO_CHANGE for the body-only edit. |

The L2 replay exercised five existing families, comprising five baselines and
15 comparisons. The C binding explicitly checks field
bounds when reading dynamic index parameters and writing memory breakdowns
in [svs_c.cpp](https://github.com/intel/ScalableVectorSearch/blob/efa2d48c9f88e2ec1afd024e5f9dd9ec9f33895f/bindings/c/src/svs_c.cpp).
The new client keeps full backing storage for the truncated logical extent,
so the sentinel safely exposes both incorrect access directions.

## Remaining evidence

- Stable numeric error codes, versioned C++ namespaces and opaque handle
  representation already have enum, symbol binding and layout mechanisms
  in Forge. Product-specific old-client mutations for these contracts remain
  unverified in this sweep.
- For callback tables the C adapter also checks a minimum table size.
  A future representative should pass a shorter operations prefix and observe
  rejection before callback dispatch. The sized data-record scenario checks
  optional field access and does not exercise this rejection policy.
- Caller-owned versus library-owned result buffers merit an allocator/deleter
  witness tied to the public C cleanup function. Existing ownership mutations
  are a starting point. A matching typedef or an unchanged export cannot
  verify correct release behavior.

Run the following with the project environment active and CastXML, clang++ and
a C compiler on PATH. The product replay builds the upstream Status translation
unit and isolates export withdrawal. The existing released-runtime profile
in scripts/l2_real_profiles.py remains the separate release-to-release test.

```sh
python -m abiforge.run.l2_representatives /tmp/svs-l2 --frontend castxml \
  --family layout --family callback_table --family virtual \
  --family member_return --family callback_return
pytest -q tests/test_abiforge_sized_record.py
research/svs-coverage/replay.sh /path/to/ScalableVectorSearch
```
