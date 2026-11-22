#!/usr/bin/env bash
set -eu
dest="$PWD/provider-move.csv"
bundle_dest="$PWD/provider-move-bundle.csv"
dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT
cd "$dir"
mkdir old moved missing
echo 'template<class T> T twice(T x){return x+x;} extern template int twice(int);' >public.hpp
echo '#include "public.hpp"' >def.cpp
echo 'template int twice(int);' >>def.cpp
echo '#include "public.hpp"' >client.cpp
echo 'int main(){return twice(4)!=8;}' >>client.cpp
g++ -O0 -shared -fPIC -s def.cpp -o old/libapi.so
g++ -O0 client.cpp -Lold -lapi -o client
g++ -O0 -shared -fPIC -s def.cpp -o moved/libowner.so
echo 'extern "C" void anchor(){}' >anchor.cpp
g++ -shared -fPIC -s anchor.cpp -Lmoved -Wl,--no-as-needed -lowner '-Wl,-rpath,$ORIGIN' -o moved/libapi.so
cp moved/libapi.so missing/
echo 'case,client_exit,single_provider_verdict' >"$dest"
echo "case,client_exit,release_verdict,bundle_verdict,bundle_findings" >"$bundle_dest"
LD_LIBRARY_PATH=old ./client
for side in moved missing; do
 rc=0
 LD_LIBRARY_PATH=$side ./client 2>/dev/null || rc=$?
 if [[ $side == moved ]]; then test "$rc" = 0; else test "$rc" != 0; fi
 python -m abicheck.cli compare old/libapi.so "$side/libapi.so" -o json=result.json >/dev/null 2>&1 || test "$?" = 4
 verdict=$(python -c 'import json; print(json.load(open("result.json"))["verdict"])')
 echo "$side,$rc,$verdict" >>"$dest"
 python -m abicheck.cli compare old "$side" -o json=bundle.json >/dev/null 2>&1 || test "$?" = 4
 python - "$side" "$rc" >>"$bundle_dest" <<'PYTHON'
import csv
import json
import sys
with open("bundle.json") as f:
    report = json.load(f)
csv.writer(sys.stdout, lineterminator="\n").writerow([
    *sys.argv[1:], report["verdict"], report["bundle_verdict"], len(report["bundle_findings"])
])
PYTHON
done
