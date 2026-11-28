#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Arguments: SVS checkout and the fmt checkout selected by its cmake/fmt.cmake.
set -eu
src=$(realpath "${1:?SVS checkout required}")
fmt=$(realpath "${2:?fmt checkout required}")
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
cd "$out"
mkdir -p old/svs/c enum/svs/c namespace/svs/runtime
inc="$src/bindings/c/include"
python - "$src/bindings/c" <<'PYCONFIG'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
version = re.search(r"project\(svs_c_api VERSION (\d+\.\d+\.\d+)", (root / "CMakeLists.txt").read_text())[1]
header = (root / "include/svs/c/svs_c_version.h.in").read_text().replace("@svs_c_api_VERSION@", version)
for key, value in zip(("MAJOR", "MINOR", "PATCH"), version.split("."), strict=True):
    header = header.replace(f"@svs_c_api_VERSION_{key}@", value)
Path("old/svs/c/svs_c_version.h").write_text(header)
PYCONFIG
sed 's/SVS_ERROR_INVALID_ARGUMENT = 2,/SVS_ERROR_INVALID_ARGUMENT = 22,/' \
    "$inc/svs/c/svs_c.h" >enum/svs/c/svs_c.h
cat >error_client.c <<'C'
#include <svs/c/svs_c.h>
int main(void) { return svs_error_get_code(NULL) != SVS_ERROR_INVALID_ARGUMENT; }
C
for variant in old enum; do
    g++ -std=c++20 -g -fPIC -shared -I"$variant" -Iold -I"$inc" -I"$src/include" \
        -I"$fmt/include" "$src/bindings/c/src/error.cpp" -o "$variant/liberrors.so"
done
cc -Iold -I"$inc" error_client.c -Lold -lerrors -o error_client
cpp="$src/bindings/cpp/include"
sed -e 's/inline namespace v0 {}/inline namespace v1 {}/' \
    -e 's/SVS_DECLARE_NAMESPACE_VERSION_0 inline namespace SVS_API_VERSION_NS(0)/SVS_DECLARE_NAMESPACE_VERSION_0 inline namespace SVS_API_VERSION_NS(1)/' \
    "$cpp/svs/runtime/version.h" >namespace/svs/runtime/version.h
for variant in old namespace; do
    g++ -std=c++20 -g -shared -fPIC -I"$variant" -I"$cpp" \
        "$src/bindings/cpp/src/api_defs.cpp" -o "$variant/libstatus.so"
done
cat >status_client.cpp <<'CPP'
#include <svs/runtime/api_defs.h>
int main() { return svs::runtime::Status(svs::runtime::ErrorCode::INVALID_ARGUMENT, "bad").ok(); }
CPP
g++ -std=c++20 -I"$cpp" status_client.cpp -Lold -lstatus -o status_client
for client in error_client status_client; do LD_LIBRARY_PATH="$PWD/old" "./$client"; done
status=0
LD_LIBRARY_PATH="$PWD/enum" ./error_client || status=$?
[[ $status == 1 ]]
status=0
LD_LIBRARY_PATH="$PWD/namespace" ./status_client 2>loader.log || status=$?
[[ $status == 127 ]]
grep -q 'undefined symbol: .*2v06Status' loader.log
for pair in 'enum errors' 'namespace status'; do
    read -r variant lib <<<"$pair"
    status=0
    abicheck compare "old/lib$lib.so" "$variant/lib$lib.so" -o "json=$variant.json" || status=$?
    python - "$variant.json" "$status" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
assert (sys.argv[2], report["verdict"]) == ("4", "BREAKING")
findings = {(c["kind"], c["symbol"]) for c in report["changes"]}
if sys.argv[1] == "enum.json":
    assert ("enum_member_value_changed", "svs_error_code::SVS_ERROR_INVALID_ARGUMENT") in findings
else:
    assert ("func_removed", "_ZN3svs7runtime2v06Status13store_messageEPKc") in findings
    assert any(kind == "inline_namespace_moved" for kind, _ in findings)
print(sys.argv[1], report["verdict"])
PY
done
