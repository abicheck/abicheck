// Minimal public header used to capture a REAL multi-document DPC++
// `-ast-dump=json` stream (see ../README.md, Fixture 1). Deliberately kept
// tiny -- this fixture exists only to give Phase D's stream parser a real
// host+device document boundary to design against, not to exercise a real
// SYCL kernel (no <sycl/sycl.hpp> include, which alone would balloon the
// capture to gigabytes -- `-fsycl` splits into a host + device compilation
// pass regardless of whether the translation unit contains an actual
// offloaded kernel).
struct Point {
    int x;
    int y;
};

int add(int a, int b);
