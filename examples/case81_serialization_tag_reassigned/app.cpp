// case81 — consumer saves a tag and later loads it.
// Under v1 the bytes 0x1002 mean knn_model; under v2 they mean
// linear_regression. Symbols all link, nothing crashes, model
// silently deserializes as the wrong class.
#include "v1.h"
#include <cstdio>

int main() {
    std::printf("knn_model_tag       = 0x%lx\n", (unsigned long)mylib::serialization_tag_for_knn_model());
    std::printf("linear_regression   = 0x%lx\n", (unsigned long)mylib::serialization_tag_for_linear_regression());
    return 0;
}
