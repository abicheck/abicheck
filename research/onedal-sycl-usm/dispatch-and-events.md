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

These witnesses directly call 14 of the 59 inventory rows. Seven generated
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

The runtime test detects the dropped dependency on this schedule. A delayed
consumer could observe 42 even with the mutation. A successful single run
cannot establish ordering in general.

## Remaining witnesses

Prioritize queue read/write contracts for CSR (including both pull overloads),
caller-owned row/column buffers, push, and chunked arrays. Next exercise
multi-rank USM communicators and preview dispatch, multi-block partial updates,
and real Level Zero execution. Report a direct declaration witness, an
independent mutation and its checker verdict separately for each extension.
