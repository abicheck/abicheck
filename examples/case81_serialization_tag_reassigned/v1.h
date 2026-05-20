// case81 v1 — DAAL-style polymorphic serialization tag IDs.
//
// DAAL's daal::services::SerializationIface assigns each serializable
// class a uint64 tag ID. Models are persisted with this ID, then on
// deserialization the registry maps ID -> factory. Reassigning an ID
// to a different class makes every previously-saved model unreadable
// (and worse — silently deserializes as the wrong type).
#pragma once
#include <cstdint>

namespace mylib {

constexpr std::uint64_t kmeans_model_tag      = 0x1001;
constexpr std::uint64_t knn_model_tag         = 0x1002;
constexpr std::uint64_t linear_regression_tag = 0x1003;
constexpr std::uint64_t decision_forest_tag   = 0x1004;

extern "C" std::uint64_t serialization_tag_for_kmeans_model();
extern "C" std::uint64_t serialization_tag_for_knn_model();
extern "C" std::uint64_t serialization_tag_for_linear_regression();
extern "C" std::uint64_t serialization_tag_for_decision_forest();

}  // namespace mylib
