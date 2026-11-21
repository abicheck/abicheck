#!/usr/bin/env bash
set -e
src=$(cd "$(dirname "$0")" && pwd)
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
cd "$out"
for change in baseline changed; do
    sym=() reg=() life=()
    if [[ $change = changed ]]; then
        sym=(-DFLIP)
        reg=(-fcall-used-r12)
        life=(-Wl,-z,nodelete)
    fi
    gcc -shared -fPIC "${sym[@]}" "$src/lib.c" "-Wl,--version-script=$src/map" -o libtest.so
    gcc -O2 -shared -fPIC "${reg[@]}" "$src/reg.c" -o libreg.so
    gcc -shared -fPIC "${life[@]}" "$src/lifetime.c" -o life.so
    if [[ $change = baseline ]]; then
        gcc "$src/client.c" -L. -ltest -Wl,-rpath,'$ORIGIN' -ldl -o lookup
        gcc "$src/r.c" "$src/check.S" -L. -lreg -Wl,-rpath,'$ORIGIN' -o register
        gcc "$src/reload.c" -ldl -o reload
    fi
    echo "$change"
    ./lookup
    result=0
    ./register || result=$?
    echo "register mismatch=$result"
    ./reload
done
