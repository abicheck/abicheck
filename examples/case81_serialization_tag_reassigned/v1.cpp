#include "v1.h"

namespace mylib {

extern "C" std::uint64_t serialization_tag_for_kmeans_model()      { return kmeans_model_tag; }
extern "C" std::uint64_t serialization_tag_for_knn_model()         { return knn_model_tag; }
extern "C" std::uint64_t serialization_tag_for_linear_regression() { return linear_regression_tag; }
extern "C" std::uint64_t serialization_tag_for_decision_forest()   { return decision_forest_tag; }

}  // namespace mylib
