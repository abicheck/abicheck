#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Run with a configured SYCL compiler and CPU runtime. DAL_ROOT enables the SDK client.
set -eu
src=$(cd "$(dirname "$0")" && pwd)
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT
cxx=${CXX:-icpx}
export ONEAPI_DEVICE_SELECTOR=opencl:cpu
for variant in baseline drop; do
    mkdir "$build/$variant"
    flags=()
    if [[ $variant == drop ]]; then flags=(-DDROP_OWNER); fi
    "$cxx" -std=c++17 -fsycl -shared -fPIC "${flags[@]}" "$src/provider.cpp" -o "$build/$variant/libpending.so"
done

clients=(generic)
if [[ -n ${DAL_ROOT:-} ]]; then clients+=(array); fi
for client in "${clients[@]}"; do
    flags=()
    if [[ $client == array ]]; then
        flags=(-DWITH_ONEDAL -DONEDAL_DATA_PARALLEL "-I$DAL_ROOT/include" "-L$DAL_ROOT/lib"
               -Wl,--no-as-needed -l:libonedal_dpc.so.3 -l:libonedal_parameters_dpc.so.3
               -l:libonedal_core.so.3 -l:libonedal_thread.so.3)
    fi
    "$cxx" -std=c++17 -fsycl "$src/client.cpp" "${flags[@]}" -L"$build/baseline" -lpending -o "$build/$client"
    for variant in baseline drop; do
        status=0
        observation=$(LD_LIBRARY_PATH="$build/$variant${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" timeout 60 "$build/$client") || status=$?
        echo "$client $variant: $observation (exit $status)"
        if [[ $variant == baseline ]]; then
            [[ $status == 0 && $observation == '0 42 1' ]]
        else
            [[ $status == 2 && $observation == '1 -1 1' ]]
        fi
    done
done
