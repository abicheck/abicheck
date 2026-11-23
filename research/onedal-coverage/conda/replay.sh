#!/usr/bin/env bash
set -euo pipefail
src=$(realpath "${1:?pass oneDAL checkout}")
dest=$(mktemp -d)
p=/tmp/dal-2025.9.0
python make_probe.py "$src"
run() {
  local source=$1 api=$2 cwd=$3 exe="$dest/client"
  shift 3
  local libs=("$p"/lib/libonedal{_core,_thread}.so.3)
  if [[ $api == oneapi ]]; then libs+=("$p"/lib/libonedal{,_parameters}.so.3); fi
  g++ -O1 -std=c++17 -I"$p/include" -I"$p/include/dal" -I"$src/examples/daal/cpp/source/utils" -I"$src/examples/oneapi/cpp/source" "$source" -Wl,--no-as-needed "${libs[@]}" -Wl,-rpath-link,"$p/lib" -o "$exe"
  for v in 2025.9.0 2025.11.0; do
    (cd "$cwd" && LD_LIBRARY_PATH="/tmp/dal-$v/lib" "$exe" "$@") >"$dest/$v"
  done
  cmp "$dest/2025.9.0" "$dest/2025.11.0"
  echo "PASS $source"
}
legacy="$src/examples/daal/cpp"
run probe.cpp legacy "$legacy" "$src"/examples/daal/data/batch/decision_tree_{train,prune}.csv
for f in "$legacy"/source/{covariance/{cov,cor},pca/pca_cor}_dense_batch.cpp "$legacy"/source/{cholesky,qr,svd,sorting,quantiles,distance,normalization,kernel_function,distributions,pivoted_qr,moments}/*dense_batch.cpp; do
  run "$f" legacy "$legacy"
done
for n in linear_kernel polynomial_kernel rbf_kernel; do
  run "$src/examples/oneapi/cpp/source/$n/${n}_dense_batch.cpp" oneapi "$src"
done
