// case83 v1 — multi-ISA dispatched algorithm. Each algorithm exports a
// per-ISA specialization symbol (avx512/avx2/sse42/scalar) plus a top-
// level dispatcher that selects one at runtime.
//
// oneDAL's libonedal_core.so follows this convention: each kernel exists
// in several ISA-specialized variants whose mangled names embed the ISA
// token (e.g. `daal_dispatch_*_avx512_*`).
#pragma once

namespace mylib {

// Dispatcher entry points (always present).
int kmeans_compute (int n);
int knn_compute    (int n);
int linreg_compute (int n);

// ISA-specific specializations (also exported so callers can pin).
int kmeans_compute_avx512(int n);
int kmeans_compute_avx2  (int n);
int kmeans_compute_sse42 (int n);
int kmeans_compute_scalar(int n);

int knn_compute_avx512(int n);
int knn_compute_avx2  (int n);
int knn_compute_sse42 (int n);
int knn_compute_scalar(int n);

int linreg_compute_avx512(int n);
int linreg_compute_avx2  (int n);
int linreg_compute_sse42 (int n);
int linreg_compute_scalar(int n);

}  // namespace mylib
