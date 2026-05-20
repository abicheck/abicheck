// case83 v2 — AVX-512 ISA support dropped (binary size reduction).
// Every `*_avx512_*` symbol disappears in one release. Dispatcher
// continues to work for callers that don't pin a specific ISA.
#pragma once

namespace mylib {

int kmeans_compute (int n);
int knn_compute    (int n);
int linreg_compute (int n);

// AVX-512 specializations removed across the board.
int kmeans_compute_avx2  (int n);
int kmeans_compute_sse42 (int n);
int kmeans_compute_scalar(int n);

int knn_compute_avx2  (int n);
int knn_compute_sse42 (int n);
int knn_compute_scalar(int n);

int linreg_compute_avx2  (int n);
int linreg_compute_sse42 (int n);
int linreg_compute_scalar(int n);

}  // namespace mylib
