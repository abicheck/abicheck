// case81 v1 — DAAL-style polymorphic serialization tag IDs.
//
// DAAL's daal::services::SerializationIface assigns each serializable
// class a uint64 tag ID. Models are persisted with this ID, then on
// deserialization the registry maps ID -> factory. Reassigning an ID
// to a different class makes every previously-saved model unreadable
// (and worse — silently deserializes as the wrong type).
//
// Implementation note: the tags are modelled as an ``enum class`` so the
// integer values land in DWARF (the value of a constexpr namespace-scope
// constant is not always captured by every dumper). The detector looks
// for tag-shaped enum names regardless of the storage mechanism.
#pragma once
#include <cstdint>

namespace mylib {

enum class SerializationTag : std::uint64_t {
    kmeans_model      = 0x1001,
    knn_model         = 0x1002,
    linear_regression = 0x1003,
    decision_forest   = 0x1004,
};

extern "C" std::uint64_t serialization_tag_for_kmeans_model();
extern "C" std::uint64_t serialization_tag_for_knn_model();
extern "C" std::uint64_t serialization_tag_for_linear_regression();
extern "C" std::uint64_t serialization_tag_for_decision_forest();

}  // namespace mylib
