# Cross-queue USM consumer

Build once with Intel DPC++ 2025.0.4 and the oneDAL 2025.2 SDK. Set the three prefixes to your installations. For a shared conda environment, use its prefix for all three.

```bash
SDK=/absolute/path/to/compiler
DAL=/absolute/path/to/onedal
RUNTIME=/absolute/path/to/opencl-runtime
export LD_LIBRARY_PATH="$DAL/lib:$RUNTIME/lib:$SDK/lib"
"$SDK/bin/icpx" -std=c++17 -fsycl -DONEDAL_DATA_PARALLEL -I"$DAL/include" event_consumer.cpp -L"$DAL/lib" -Wl,--no-as-needed -l:libonedal_dpc.so.3 -l:libonedal_parameters_dpc.so.3 -l:libonedal_core.so.3 -l:libonedal_thread.so.3 -o consumer
OCL_ICD_VENDORS="$RUNTIME/etc/OpenCL/vendors" ./consumer
```

The producer kernel writes 256 floats. The consumer queue wraps the pointer and producer event in a homogen_table, pulls rows, and checks every value. On Intel OpenCL CPU, host, shared and device USM each passed on oneDAL 2025.2 (runtime 2025.0.4) and 2025.9 and 2025.11 (runtime 2025.3.2). A passing run records observed ordering; scheduling may conceal a missing dependency. This consumer does not measure GPU execution.

## Scan each public interface with its own compiler

For a packaged Linux release, pair the old and new copies of each interface
before comparing their snapshots. This example uses GCC as a CPU *client*
and Intel DPC++ as a SYCL client. Select the producer compiler from the build
record if reproducing the producer configuration. Neither a filename nor a
successful header parse identifies which compiler produced a packaged binary.

| Interface in the SDK | Header entry point | Example client configuration |
| --- | --- | --- |
| Legacy DAAL, `libonedal_core.so` | `daal.h` | CPU C++ |
| oneDAL CPU, `libonedal.so`, `libonedal_parameters.so` | `oneapi/dal.hpp` or the matching parameters header | CPU C++ |
| oneDAL SYCL, `libonedal_dpc.so`, `libonedal_parameters_dpc.so` | Same oneDAL headers with the data-parallel API enabled | icpx, C++17, `-fsycl`, `ONEDAL_DATA_PARALLEL` |

The following scoped scan uses the same public header on both interfaces.
Install the matching native GCC plugin first (see [Native GCC](../../docs/use/native-gcc.md)).
Use two explicit config files, with the compiler paths installed on your machine.

```yaml
# cpu.yml
compile:
  frontend: gcc
  compiler: /usr/bin/g++-14
  std: c++17
```

```yaml
# dpc.yml
compile:
  frontend: clang
  compiler: /opt/intel/oneapi/compiler/latest/bin/icpx
  std: c++17
  defines: [ONEDAL_DATA_PARALLEL]
  options: [-fsycl, --offload-new-driver]
  frontend_context: host
```

`--offload-new-driver` selects the driver mode exercised below. Use the
actual offload mode recorded for the build. The SYCL host pass exposes the
queue-taking calls that user code links against. Device evidence requires
a separate scan. With shell variables `OLD` and `NEW` set to SDK prefixes:

```sh
for side in old new; do
  case "$side" in old) sdk=$OLD ;; new) sdk=$NEW ;; esac
  for profile in cpu dpc; do
    case "$profile" in cpu) lib=libonedal ;; dpc) lib=libonedal_dpc ;; esac
    abicheck dump "$sdk/lib/$lib.so.3" -I "$sdk/include" \
      -H "$sdk/include/oneapi/dal/table/homogen.hpp" \
      --config "$profile.yml" -o "$side-$profile.json.gz" || exit 1
  done
done
abicheck compare old-cpu.json.gz new-cpu.json.gz -o json=cpu-report.json
abicheck compare old-dpc.json.gz new-dpc.json.gz -o json=dpc-report.json
```

For project CI, declare separate target checks selecting the CPU and DPC++
profiles. Bind each profile to its installed executable through a trusted
bindings file. Set the profile's frontend, standard, args and ABI macros.
A second supported consumer of the same binary gets its own baseline and
candidate dumps. See the [profile schema](../../docs/reference/project-targets-schema.md#profiles).

On the 2025.2.0 conda CPU library, GCC 14.2 compiled a client calling
`homogen_table::wrap(const float*, rows, columns)`. Its native GCC snapshot
recorded `homogen_table::kind` and `get_data`, but omitted the uninstantiated
`wrap` overloads from the function table. Treat that lane as partial for
template declarations. In a separate source-replay check, icpx 2025.0.4
produced only the host declaration for `--offload-host-only` and only the
device declaration for `--offload-device-only` with `--offload-new-driver`.
Previously both replays failed on the injected legacy host selector.

The whole `homogen.hpp` host AST from the new driver was 1.4 GiB and the
unpruned CLI run exhausted memory on the experiment machine. The scoped
L2 header dump completed with the [precompiled SYCL header setup](dispatch-and-events.md)
and recorded three overloads declared in `homogen.hpp`, whose first
parameters are `const Data*`, `const sycl::queue&`, and
`const dal::array<Data>&`. Keep temporary outputs outside the PCH input
directory. Check the stored `ast_producer` and `ast_toolchain` as well as a
known interface-specific declaration when validating a scan profile.

### Capture both interfaces in a release baseline

Keep the two compiler profiles and their library assignments in one
version-controlled `.abicheck.yml`.

```yaml
profiles:
  cpu:
    compile: {frontend: gcc, binding: gcc14, standard: c++17}
  sycl:
    compile:
      frontend: clang
      binding: dpc
      standard: c++17
      args: [-fsycl, --offload-new-driver]
      abi_macros: {ONEDAL_DATA_PARALLEL: "1"}
release:
  bindings: {gcc14: /usr/bin/g++-14, dpc: /opt/intel/oneapi/compiler/latest/bin/icpx}
  libraries:
    libonedal.so:
      profiles: [cpu]
      headers: [include/oneapi/dal/table/homogen.hpp]
      includes: [include]
    libonedal_dpc.so:
      profiles: [sycl]
      headers: [include/oneapi/dal/table/homogen.hpp]
      includes: [include]
```

```sh
abicheck dump "$OLD" --config .abicheck.yml --bundle-facts-out baseline.json
abicheck compare baseline.json "$NEW" --config .abicheck.yml -o json=report.json
```

Pass each unpacked SDK root as the operand. Header, include and sysroot paths
resolve against that SDK; compiler executables are selected on the scan host. A compile override replaces the member's whole compiler
context. Include its standard, macros and other required switches. To check
another supported client compiler for the same library, capture a separate
baseline and compare under that profile.

On the 2025.9 conda parameter libraries, native GCC 14.2 captured
`get_max_number_of_threads()`. The icpx 2025.0.4 host pass with the
precompiled SYCL header also captured `get_max_workgroup_size(sycl::queue&)`.
Comparing the two unchanged libraries to this baseline returned `NO_CHANGE`
for both members. The two-member bundle had unresolved provider dependencies.

Removing the queue overload from the SYCL candidate header with both 2025.9
binaries unchanged gave `NO_CHANGE` for the GCC CPU member and `API_BREAK`
for the icpx SYCL member. A separate two-library CLI regression used a single
release-relative manifest with different interface macros, one stored
baseline, and two independent SDK trees. Editing only the candidate's gated
declaration reproduced the same split.
