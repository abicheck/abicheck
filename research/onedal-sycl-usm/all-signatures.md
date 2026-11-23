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

## Compatibility coverage

The 59 outcomes classify availability and executable behavior. They do not
measure 59 independent abicheck detections. The generated bridge withdrawal
has exact checks for seven symbols. Source-break severity for an explicit
event argument remains an observed checker gap. GPU execution of the five
preview algorithms, other template instantiations and per-signature break
and compatibility-preserving mutations remain separate validation work.
