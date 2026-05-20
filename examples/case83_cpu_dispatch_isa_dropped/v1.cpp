#include "v1.h"

namespace mylib {

int kmeans_compute (int n) { return n; }
int knn_compute    (int n) { return n; }
int linreg_compute (int n) { return n; }

int kmeans_compute_avx512(int n) { return n + 512; }
int kmeans_compute_avx2  (int n) { return n + 256; }
int kmeans_compute_sse42 (int n) { return n + 128; }
int kmeans_compute_scalar(int n) { return n + 1; }

int knn_compute_avx512(int n) { return n + 512; }
int knn_compute_avx2  (int n) { return n + 256; }
int knn_compute_sse42 (int n) { return n + 128; }
int knn_compute_scalar(int n) { return n + 1; }

int linreg_compute_avx512(int n) { return n + 512; }
int linreg_compute_avx2  (int n) { return n + 256; }
int linreg_compute_sse42 (int n) { return n + 128; }
int linreg_compute_scalar(int n) { return n + 1; }

}  // namespace mylib
