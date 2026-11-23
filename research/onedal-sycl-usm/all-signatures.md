# Results for the oneDAL SYCL host-signature inventory

[signature-witnesses.tsv](signature-witnesses.tsv) maps every row of inventory.tsv to
a concrete consumer and an outcome. The denominator is the 59 macro-dependent
signatures in the three named header roots in [dispatch-and-events.md](dispatch-and-events.md).
For templates the result is specific to the instantiated type, task and method.

| Observed stage | Rows | Evidence |
| --- | ---: | --- |
| Runtime success through a direct call | 47 | Numeric, ownership, shape, pointer or queue checks, including the MPI backend factory specialization |
| Runtime success through virtual dispatch | 5 | The two-rank USM communicator forwards to its MPI implementation through the five communicator_iface slots |
| Compile and link, backend rejects operation | 5 | Each preview compute/finalize_compute (covariance) and train/finalize_train/infer (linear regression) ran separately on two ranks and reported oneDAL unimplemented on the OpenCL CPU |
| Header fails instantiation | 1 | Deprecated array<float>::need_mutable_data(queue, alloc) invokes the implementation with two arguments where it takes zero; reproduced with all three dal-include versions |
| Missing provider definitions | 1 | preview::partial_train for float linear regression with norm_eq requires two SPMD partial_train_ops_dispatcher symbols missing at link time in all three tested provider configurations |

The runtime passes were measured with oneDAL 2025.2, 2025.9 and 2025.11 using the
runtime pairs documented in dispatch-and-events.md. MPI used Open MPI 5.0.7,
two local ranks and shared USM. The typed and raw byte overloads independently
check rank-dependent broadcast, sum reduction, gather ordering and peer exchange.
Array probes check destruction callbacks, const/mutable buffer ownership and
the deprecated event-taking wrap overload. The CSV probe checks the parsed values.
Two chunked-array rows accept a detail policy. The MPI factory requires the
backend-specific header; its generic primary template deliberately rejects
unspecialized backend types.

## Reproduction

Use the SDK compile and link command in dispatch-and-events.md, substituting
ownership_consumer.cpp, spmd_consumer.cpp or spmd_dispatch.cpp. For the two MPI
programs add the include and link flags from the MPI compiler wrapper. Set the
runtime library path and ICD as in the dispatch experiment.

    printf '7,8\n' > data.csv
    ./ownership_consumer data.csv
    mpiexec -n 2 ./spmd_consumer
    for operation in compute finalize_compute train finalize_train infer; do
        mpiexec -n 2 ./spmd_dispatch "$operation"
    done

spmd_dispatch returns 77 when the backend reports an unimplemented operation.
To reproduce the compiler failure, rebuild ownership_consumer with
-DONEDAL_PROBE_DEPRECATED_MUTABLE. To reproduce the provider link failure,
build spmd_dispatch with -DONEDAL_PROBE_SPMD_PARTIAL_TRAIN. If a future provider
links it, run that binary with the operation partial_train.

## Checker coverage for declaration withdrawals

The stock checker matched all 59 inventory entries to individual withdrawal findings on
Linux x86-64 with oneDAL 2025.2.0, icpx 2025.0.4 and the unchanged
`libonedal_dpc.so.3`. [checker-withdrawals.tsv](checker-withdrawals.tsv) records
each declaration and three Boolean results.

| Comparison | Unit of verification | Result |
| --- | --- | ---: |
| Disable the SYCL header surface, keeping the same binary and compiler profile | Exact old declaration identity in the full comparison | 59/59 identified |
| Remove one declaration identity from its defining header's L2 function list | Exact old declaration identity in an independent comparison | 59/59 identified |
| Compare identical L2 function lists | Withdrawal of the selected identity | 0/59 false removals |

To reproduce the bulk mutation, copy the SDK's `include/oneapi/dal` tree into
a private include root and place it first on the include path. Use the host
PCH and compile options in [dispatch-and-events.md](dispatch-and-events.md).
Dump the original library with one `-H` for each distinct header in the first
column of inventory.tsv, resolved under the private `include/oneapi/dal`.
Prepend `#undef ONEDAL_DATA_PARALLEL` to each of those 16 copied headers and
repeat the same dump command with a different output filename. Both dumps must
use the same include root, compiler, options and binary. From a checkout with
abicheck installed, run:

    python research/onedal-sycl-usm/checker_withdrawals.py old.json new.json > actual.tsv
    diff -u research/onedal-sycl-usm/checker-withdrawals.tsv actual.tsv

The isolated comparisons edit the declaration model, without rebuilding oneDAL.
They validate checker attribution separately from extraction. For the distinct
event-parameter edit, comparing the full library with original and modified
`homogen.hpp` now yields `API_BREAK` for
`oneapi::dal::v1::homogen_table::wrap` (six arguments before, five at most
after). The unchanged binary and host overload remain available.

These results measure source declarations. The runtime outcomes above, event
ordering, concrete template instantiations, and device/backend availability
require separate evidence.
