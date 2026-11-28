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

- Opaque handle representation has generic layout coverage. A mutation on
  the SVS implementation with a frozen client remains unverified.
- The callback table and cleanup experiments below now exercise the two
  behavioral contracts in Forge. The product evidence for these two cases is
  source inspection of StreamBuf::validate and svs_search_results_free.

## Follow-up experiments

A frozen caller passed a table with a shortened logical extent. The baseline
rejected it before invoking its callback. Removing the minimum check invoked
the callback once. A full table dispatched successfully on both libraries.
A separate cleanup mutation invoked a release callback on borrowed storage.
An owned result was released exactly once across two cleanup calls. These
probes observe execution with identical public declarations.

The C++ callback_interface family exposes a callback through an interface's
public operations pointer. Changing the calling convention of its by-value
record argument kept both table layouts fixed. The frozen caller returned
17 with the original and equivalent providers and 23 with the mutation.
On identical CastXML snapshots the old checker returned NO_CHANGE. The
updated checker attributed value_abi_trait_changed to the exported interface
and the nested callback parameter. Clang snapshots produced the same finding.
This comparison needs call-triviality evidence on both sides. A CastXML scan
that omits the optional Clang semantics pass can lack that evidence.

The threadpool operations table accepts a callback as an argument of another
callback. The generic nested_callback fixture keeps the exported function and
both table layouts fixed while changing a record's by-value passing convention
at the innermost call. The original client returned 0 with either compatible
provider and 1 with the mutant. Before the fix, Clang snapshots produced
COMPATIBLE_WITH_RISK from import changes. Both frontends now report BREAKING
with value_abi_trait_changed at return.ops.run.callback.p0.callback.p0.
The C++ record mutation tests the reusable callback shape found in the SVS
C header; the actual SVS threadpool callback receives a pointer and an index.

The buffer_stream fixture exercises two body-only contracts. Its allocation
counter and pointer checks distinguish reuse at sufficient capacity, successful
growth with contents preserved, and rejection of growth for borrowed storage.
The stream client receives two chunks followed by a zero count, once with clean
EOF and once with an error. Treating the first short read as EOF loses bytes;
discarding the terminal error preserves the bytes but changes the status.
The frozen client distinguishes all four independent mutations.

Two isolated product mutations use the pinned upstream translation units.
Changing SVS_ERROR_INVALID_ARGUMENT from 2 to 22 in the C header changes the
result of svs_error_get_code(NULL) for the old client. ELF/DWARF comparison
reports enum_member_value_changed on that enumerator. Rebuilding Status in
inline namespace v1 withdraws its v0 helper symbols. The frozen client fails
at symbol resolution (127); the checker reports func_removed on store_message
and inline_namespace_moved. Both comparisons return BREAKING (4).

Run the following with the project environment active and CastXML, clang++ and
a C compiler on PATH. The product replay builds the upstream Status translation
unit and isolates export withdrawal. The existing released-runtime profile
in scripts/l2_real_profiles.py remains the separate release-to-release test.

```sh
python -m abiforge.run.l2_representatives /tmp/svs-l2 --frontend castxml \
  --family layout --family callback_table --family virtual \
  --family member_return --family callback_return --family callback_interface --family nested_callback
pytest -q tests/test_abiforge_sized_record.py
research/svs-coverage/replay.sh /path/to/ScalableVectorSearch
research/svs-coverage/replay_identities.sh /path/to/ScalableVectorSearch /path/to/fmt-12.1.0
```
