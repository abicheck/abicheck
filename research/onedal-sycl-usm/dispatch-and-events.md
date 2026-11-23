# oneDAL SYCL dispatch and event witnesses

## Header inventory

With icpx 2025.0.4 on Linux, the C++17 host AST gained 59 distinct callable
signatures when enabling ONEDAL_DATA_PARALLEL. The sorted rows in
[inventory.tsv](inventory.tsv) were identical for dal-include 2025.2.0,
2025.9.0 and 2025.11.0. Both parses used SYCL mode. Each row combines the
header, qualified name and function type. The inventory selects public members
and free functions, excludes detail and backend scopes, and includes preview
declarations. It measures these header roots and their transitive includes.
It does not enumerate every specialization, default argument, optional MPI/CCL
adapter, device image, private implementation or runtime behavior.

    LD_LIBRARY_PATH="$SDK/lib" python inventory.py "$SDK/bin/icpx" "$DAL/include" \
        oneapi/dal.hpp oneapi/dal/algo/linear_regression.hpp \
        oneapi/dal/table/csr_accessor.hpp > actual.tsv
    diff -u inventory.tsv actual.tsv

## Direct consumer calls

| Declaration in the inventory | Witness | Observation |
| --- | --- | --- |
| v1::compute, partial_compute, finalize_compute with queue | dispatch_consumer.cpp, covariance | Batch and online 1×1 sample covariance both equal 5/3 within 1e-4 |
| v1::train, partial_train, finalize_train, infer with queue | dispatch_consumer.cpp, linear regression | Batch and online models each predict 3, 5, 7, 9 within 1e-4 |
| v2::array::empty, full, zeros, wrap with queue | abiforge.run.onedal_sycl | Count of empty allocation, initialized data and wrapped value |
| v1::homogen_table::wrap with queue | abiforge.run.onedal_sycl and dependency_consumer.cpp | Shape and visibility of producer data |
| v1::row_accessor::pull and column_accessor::pull returning dal::array | abiforge.run.onedal_sycl | First element from the queue overload |
| v1::csr_table::wrap and both v1::csr_accessor::pull queue overloads | csr_consumer.cpp | Full one-based matrix, then a zero-based one-row slice through caller-supplied arrays |
| v1::row_accessor and column_accessor, queue push and buffer-taking pull | buffer_consumer.cpp | A row write followed by an overlapping column write produces the expected six matrix elements |
| v2::chunked_array::flatten and get_data with queue | buffer_consumer.cpp | Check two unequal chunks in order and their two original USM addresses |

These witnesses directly call 23 of the 59 inventory rows. Seven generated
bridge exports were withdrawn together. The checker attributed all seven to
sycl_overload_set_removed, the unchanged control returned NO_CHANGE, the old
queue consumer failed to load and its rebuild failed. The host sibling ran and
rebuilt in both cases. These are bridge mutations, not removals from shipping
oneDAL binaries.

Compile dispatch_consumer.cpp once with the 2025.2 SDK and run the same binary
with each provider/runtime pair. The dispatch fixture checks one input and
descriptor per algorithm and uses one partial block.

    export LD_LIBRARY_PATH="$SDK/lib"
    "$SDK/bin/icpx" -std=c++17 -fsycl -DONEDAL_DATA_PARALLEL -I"$SDK/include" \
        dispatch_consumer.cpp -L"$SDK/lib" -Wl,--no-as-needed -l:libonedal_dpc.so.3 \
        -l:libonedal_parameters_dpc.so.3 -l:libonedal_core.so.3 -l:libonedal_thread.so.3 -o dispatch
    OCL_ICD_VENDORS="$RT/etc/OpenCL/vendors" LD_LIBRARY_PATH="$DAL/lib:$RT/lib:$SDK/lib" ./dispatch

| oneDAL | Intel SYCL runtime | OpenCL CPU driver on AMD EPYC 9V74 | Result |
| --- | --- | --- | --- |
| 2025.2.0 | 2025.0.4 | 2024.18.12.0.05_160000 | pass |
| 2025.9.0 and 2025.11.0 | 2025.3.2 | 2026.20.1.0.12_160000 | pass |
| 2025.9.0 and 2025.11.0 | 2025.0.4 | fails during symbol lookup | exit 127, missing sycl::_V1::detail::SubmissionInfo::SubmissionInfo() |

The loader trace on the 2025.11 lane resolved libonedal_dpc from the selected DAL
prefix and libsycl and libintelocl from the selected runtime. The failing lanes
establish a runtime incompatibility. An exact minimum runtime or GPU driver
requires intervening versions and a GPU. The Level Zero GPU selector reported
no matching device here. Run the same executable on a GPU runner with
ONEAPI_DEVICE_SELECTOR=level_zero:gpu and record its device and driver.

The CSR fixture builds with the same command by substituting csr_consumer.cpp.
It passed on all three matched CPU configurations.

The plugin scanner previously skipped both Intel 2025.0.4 adapters because
their exported dispatch root is urGetGlobalProcAddrTable. After accepting that
root, static scans retained 21 exports each for OpenCL and Level Zero in
2025.0.4 and 2025.3.2. The generated plugin campaign separately checks addition,
removal, entry-point edits and an unchanged control for this export shape.

## Event dependency mutations

    python event_mutations.py "$SDK" /tmp/onedal-event-mutations

The script changes only the queue overload of homogen_table::wrap in a private
header overlay. A producer host task writes 42 to shared USM. A mutex protects
the write and the consumer's read. The producer waits for the first read or a
two-second timeout, making a stale value observable when the wait is removed.
The current oneDAL constructor explicitly waits for dependencies. This fixture
tests host-visible readiness for this path.

| Variant | Rebuild | Observed value | Exit |
| --- | --- | ---: | ---: |
| Original forwarding | succeeds | 42 | 0 |
| Forward a copy of the same event vector | succeeds | 42 | 0 |
| Forward an empty vector, preserve public signature | succeeds | 0 | 2 |
| Remove the public event parameter | explicit-event call rejected | — | — |

The compiler also checks four qualified call expressions in event_call_forms.cpp.
Both headers accept the host call and the queue call with default arguments.
Only the original accepts an explicit event vector, with either default or
explicit layout. All eight assertions passed independently on the 2025.2, 2025.9 and 2025.11
headers (24 compiler-checked outcomes).

The runtime test detects the dropped dependency on this schedule. A delayed
consumer could observe 42 even with the mutation. A successful single run
cannot establish ordering in general.

A separate minimal L2 comparison kept a host sibling and shortened a queue
function template by one defaulted event-list argument. The checker emitted
overload_set_rerouted and classified the pair COMPATIBLE_WITH_RISK. This exposed
a source-classification gap when a formerly supported call no longer compiles.
The ordinary dual-pass scan of the full header exhausted memory (exit 137).
A second host-only L2 experiment precompiled just sycl/sycl.hpp with icpx
2025.0.4, C++17, -fno-sycl -Xclang -fsycl-is-host,
-DONEDAL_DATA_PARALLEL -D__SYCL_ANY_DEVICE_HAS_ANY_ASPECT__=1 and the SDK
include, include/sycl and include/sycl/stl_wrappers directories. Use the same
flags in compile.options/defines and add -include-pch and the absolute PCH
path for both dumps. Keep temporary output outside the PCH input directory.
Both dumps used the original libonedal_dpc.so.3. The complete public
homogen.hpp remained outside the PCH on each side. L2 extraction took 2.0 s
and 1.9 s, respectively, and both dumps explicitly contain the expected queue
signature. These timings exclude PCH creation and binary extraction. Comparing both snapshots through abicheck.checker.compare
returned COMPATIBLE_WITH_RISK with one overload_set_rerouted for wrap. The two
compiler-rejected explicit-event calls are a demonstrated false negative for
source-break severity. Full CLI JSON rendering hit the memory limit after
collecting 51,495 findings (51,489 exported_not_public when the full binary is
paired with this single public header).

## Follow-up covering all 59 rows

See [all-signatures.md](all-signatures.md) for the subsequent two-rank and ownership experiments, checker fix, per-signature withdrawal results, and updated API_BREAK verdict for the event-parameter edit.

## Remaining witnesses at the time of the initial experiment

The buffer fixture uses the internal homogen_table_builder to obtain writable accessors. It passed on all three matched CPU configurations using shared USM. Build it like dispatch_consumer.cpp. Prioritize device allocations and writes across queue contexts. Next exercise
multi-rank USM communicators and preview dispatch, multi-block partial updates,
and real Level Zero execution. Report a direct declaration witness, an
independent mutation and its checker verdict separately for each extension.

## Removing only the default event list

The `remove_default` variant in event_mutations.py changes the queue overload's
`dependencies = {}` to `dependencies`. Its parameter types, implementation,
layout default and library binary are identical. The four-argument queue call
now fails overload resolution. The host call and both explicit-event calls
continue to compile. icpx 2025.0.4 checked all four forms before and after
the edit on the 2025.2, 2025.9 and 2025.11 headers. On the 2025.2 OpenCL CPU,
the consumer passing its producer event read 42 with both headers.

With the full 2025.2 library, the ordinary checker now reports
`param_default_value_removed` for `oneapi::dal::v1::homogen_table::wrap`
and grades the pair `API_BREAK`. This comparison previously produced
`NO_CHANGE` when the linker-name index retained a host sibling. The
per-signature withdrawal results in all-signatures.md still apply.
