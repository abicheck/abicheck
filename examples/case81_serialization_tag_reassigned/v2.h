// case81 v2 — knn_model_tag and linear_regression_tag SWAPPED values.
//
// Symbols, signatures, types all unchanged. Saved v1 knn_model files
// deserialize as linear_regression and vice versa. Silent data
// corruption with no link-time or load-time error.
#pragma once
#include <cstdint>

namespace mylib {

constexpr std::uint64_t kmeans_model_tag      = 0x1001;
constexpr std::uint64_t knn_model_tag         = 0x1003;   // <-- WAS 0x1002
constexpr std::uint64_t linear_regression_tag = 0x1002;   // <-- WAS 0x1003
constexpr std::uint64_t decision_forest_tag   = 0x1004;

extern "C" std::uint64_t serialization_tag_for_kmeans_model();
extern "C" std::uint64_t serialization_tag_for_knn_model();
extern "C" std::uint64_t serialization_tag_for_linear_regression();
extern "C" std::uint64_t serialization_tag_for_decision_forest();

}  // namespace mylib
