#include "v2.h"

namespace mylib {

extern "C" std::uint64_t serialization_tag_for_kmeans_model() {
    return static_cast<std::uint64_t>(SerializationTag::kmeans_model);
}
extern "C" std::uint64_t serialization_tag_for_knn_model() {
    return static_cast<std::uint64_t>(SerializationTag::knn_model);
}
extern "C" std::uint64_t serialization_tag_for_linear_regression() {
    return static_cast<std::uint64_t>(SerializationTag::linear_regression);
}
extern "C" std::uint64_t serialization_tag_for_decision_forest() {
    return static_cast<std::uint64_t>(SerializationTag::decision_forest);
}

}  // namespace mylib
