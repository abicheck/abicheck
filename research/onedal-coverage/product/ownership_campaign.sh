#!/usr/bin/env bash
set -eu
old=$1
new=$2
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
flags=(-std=c++17 -O1 -I"$old/include" -I"$old/include/dal")
g++ "${flags[@]}" ownership.cpp "$old"/lib/libonedal{_core,_thread}.so.3 -o "$out/client"
echo 'environment,operation,implementation,exit'
for operation in COPY ASSIGN; do
    for mode in BASELINE FORWARD MUTANT; do
        preload=
        expected=0
        if [[ $mode != BASELINE ]]; then
            preload="$out/shim.so"
            g++ "${flags[@]}" -shared -fPIC -D"$operation" -D"$mode" status_copy.cpp -ldl -o "$preload"
        fi
        if [[ $mode == MUTANT ]]; then
            if [[ $operation == COPY ]]; then expected=11; else expected=12; fi
        fi
        for prefix in "$old" "$new"; do
            rc=0
            LD_LIBRARY_PATH="$prefix/lib" LD_PRELOAD="$preload" "$out/client" || rc=$?
            echo "$(basename "$prefix"),$operation,$mode,$rc"
            [[ $rc == "$expected" ]] || exit 1
        done
    done
done
