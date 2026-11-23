#!/usr/bin/env bash
# Linux x86-64 SysV. Build the old client once and swap one public operation.
set -eu
old=$1
new=$2
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
symbol=_ZN4daal8services10interface16Status5clearEv
g++ -std=c++17 -O1 -I"$old/include" -I"$old/include/dal" ownership.cpp \
    "$old"/lib/libonedal{_core,_thread}.so.3 -o "$out/client"
cat > "$out/clear.c" <<SRC
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdlib.h>
void $symbol(void *self) {
#ifdef FORWARD
    void (*original)(void *) = dlsym(RTLD_NEXT, "$symbol");
    if (!original) abort();
    original(self);
#endif
}
SRC
gcc -shared -fPIC -DFORWARD "$out/clear.c" -ldl -o "$out/forward.so"
gcc -shared -fPIC "$out/clear.c" -o "$out/no_op.so"
echo 'environment,implementation,exit'
for prefix in "$old" "$new"; do
    for implementation in baseline forward no_op; do
        expected=0
        preload=
        if [[ $implementation != baseline ]]; then preload=$out/$implementation.so; fi
        if [[ $implementation == no_op ]]; then expected=3; fi
        result=0
        LD_PRELOAD="$preload" LD_LIBRARY_PATH="$prefix/lib" "$out/client" || result=$?
        echo "$(basename "$prefix"),$implementation,$result"
        [[ $result == "$expected" ]] || exit 1
    done
done
