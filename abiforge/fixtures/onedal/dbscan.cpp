// SPDX-License-Identifier: Apache-2.0
#include <cstdint>
#include <oneapi/dal/algo/dbscan.hpp>
#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/row_accessor.hpp>

namespace dal = oneapi::dal;
namespace options = dal::dbscan::result_options;

template <typename Float>
bool clusters_match(bool save_memory) {
    const Float points[] = {0, 1, 2, 20, 21, 22, 100};
    dal::dbscan::descriptor<Float> descriptor(2.5, 3);
    descriptor.set_mem_save_mode(save_memory);
    descriptor.set_result_options(options::responses | options::core_observation_indices |
                                  options::core_observations);
    auto result = dal::compute(descriptor, dal::homogen_table::wrap(points, 7, 1));
    const auto& responses = result.get_responses();
    const auto& indices = result.get_core_observation_indices();
    const auto& observations = result.get_core_observations();
    if (result.get_cluster_count() != 2 || responses.get_row_count() != 7 ||
        indices.get_row_count() != 6 || observations.get_row_count() != 6 ||
        responses.get_column_count() != 1 || indices.get_column_count() != 1 ||
        observations.get_column_count() != 1) return false;
    const auto labels = dal::row_accessor<const std::int32_t>(responses).pull();
    if (labels[0] < 0 || labels[3] < 0 || labels[0] == labels[3] || labels[6] != -1) return false;
    const auto rows = dal::row_accessor<const std::int32_t>(indices).pull();
    const auto values = dal::row_accessor<const Float>(observations).pull();
    bool seen[6] = {};
    for (int i = 0; i < 6; ++i) {
        if (labels[i] != labels[(i / 3) * 3]) return false;
        const auto row = rows[i];
        if (row < 0 || row >= 6 || seen[row] || values[i] != points[row]) return false;
        seen[row] = true;
    }
    return true;
}

int main() {
    return clusters_match<float>(false) && clusters_match<float>(true) &&
           clusters_match<double>(false) && clusters_match<double>(true) ? 0 : 1;
}
