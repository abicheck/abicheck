# Cross-queue USM consumer

Build once with Intel DPC++ 2025.0.4 and the oneDAL 2025.2 SDK. Set SDK to the absolute compiler prefix.

```bash
export LD_LIBRARY_PATH="$SDK/lib"
"$SDK/bin/icpx" -std=c++17 -fsycl -DONEDAL_DATA_PARALLEL -I"$SDK/include" event_consumer.cpp -L"$SDK/lib" -Wl,--no-as-needed -l:libonedal_dpc.so.3 -l:libonedal_parameters_dpc.so.3 -l:libonedal_core.so.3 -l:libonedal_thread.so.3 -o consumer
OCL_ICD_VENDORS="$RUNTIME/etc/OpenCL/vendors" LD_LIBRARY_PATH="$DAL/lib:$RUNTIME/lib:$SDK/lib" ./consumer
```

The producer kernel writes 256 floats. The consumer queue wraps the pointer and producer event in a homogen_table, pulls rows, and checks every value. On Intel OpenCL CPU, host, shared and device USM each passed on oneDAL 2025.2 (runtime 2025.0.4) and 2025.9 and 2025.11 (runtime 2025.3.2). A passing run records observed ordering; scheduling may conceal a missing dependency. This consumer does not measure GPU execution.
