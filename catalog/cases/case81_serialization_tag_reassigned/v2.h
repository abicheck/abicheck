// case81 v2 — knn_model and linear_regression SWAPPED values.
//
// Symbols, signatures, types all unchanged. Saved v1 knn_model files
// deserialize as linear_regression and vice versa. Silent data
// corruption with no link-time or load-time error.
#pragma once
#include <cstdint>

namespace mylib {

enum class SerializationTag : std::uint64_t {
    kmeans_model      = 0x1001,
    knn_model         = 0x1003,   // <-- WAS 0x1002
    linear_regression = 0x1002,   // <-- WAS 0x1003
    decision_forest   = 0x1004,
};

extern "C" std::uint64_t serialization_tag_for_kmeans_model();
extern "C" std::uint64_t serialization_tag_for_knn_model();
extern "C" std::uint64_t serialization_tag_for_linear_regression();
extern "C" std::uint64_t serialization_tag_for_decision_forest();

}  // namespace mylib
