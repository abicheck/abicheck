#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Run in the abicheck environment with a checkout of the pinned SVS revision.
set -eu
src=$(realpath "${1:?supply an SVS checkout}")
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
cd "$out"
mkdir old hidden
echo '{ local: *store_message*; };' > exports
for variant in old hidden; do
    flags=()
    if [[ $variant == hidden ]]; then flags=(-Wl,--version-script=exports); fi
    g++ -std=c++20 -g -shared -fPIC -I"$src/bindings/cpp/include" \
        "$src/bindings/cpp/src/api_defs.cpp" "${flags[@]}" -o "$variant/libstatus.so"
done
cat > client.cpp <<'CPP'
#include <cstring>
#include <svs/runtime/api_defs.h>
int main() {
    using namespace svs::runtime;
    Status error(ErrorCode::INVALID_ARGUMENT, "invalid");
    Status copy(error);
    return copy.ok() || std::strcmp(copy.message(), "invalid");
}
CPP
g++ -std=c++20 -I"$src/bindings/cpp/include" client.cpp -Lold -lstatus -o client
LD_LIBRARY_PATH="$PWD/old" ./client
status=0
LD_LIBRARY_PATH="$PWD/hidden" ./client 2>loader.log || status=$?
[[ $status == 127 ]]
grep -q 'undefined symbol: .*store_message' loader.log
status=0
abicheck compare old/libstatus.so hidden/libstatus.so -o json=report.json || status=$?
[[ $status == 4 ]]
python - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("report.json").read_text())
assert report["verdict"] == "BREAKING"
assert any(change["kind"] == "func_visibility_changed" and "store_message" in change["symbol"]
           for change in report["changes"])
PY
