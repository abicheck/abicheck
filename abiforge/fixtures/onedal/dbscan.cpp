// SPDX-License-Identifier: Apache-2.0
#include <cstdint>
#include <oneapi/dal/algo/dbscan.hpp>
#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/row_accessor.hpp>

namespace dal = oneapi::dal;
namespace options = dal::dbscan::result_options;

template <typename Float>
bool clusters_match(bool save_memory, bool weighted) {
    const Float points[] = {0, 1, 2, 20, 21, 22, 100};
    dal::dbscan::descriptor<Float> descriptor(2.5, 3);
    descriptor.set_mem_save_mode(save_memory);
    descriptor.set_result_options(options::responses | options::core_observation_indices |
                                  options::core_observations);
    const Float weights[] = {1, 1, 1, 1, 1, 1, 3};
    dal::dbscan::compute_input<> input;
    input.set_data(dal::homogen_table::wrap(points, 7, 1));
    if (weighted) {
        input.set_weights(dal::homogen_table::wrap(weights, 7, 1));
        if (input.get_weights().get_row_count() != 7) return false;
    }
    const auto result = dal::compute(descriptor, input);
    const int core_count = weighted ? 7 : 6;
    const auto& responses = result.get_responses();
    const auto& indices = result.get_core_observation_indices();
    const auto& observations = result.get_core_observations();
    if (result.get_cluster_count() != (weighted ? 3 : 2) || responses.get_row_count() != 7 ||
        indices.get_row_count() != core_count || observations.get_row_count() != core_count ||
        responses.get_column_count() != 1 || indices.get_column_count() != 1 ||
        observations.get_column_count() != 1) return false;
    const auto labels = dal::row_accessor<const std::int32_t>(responses).pull();
    if (labels[0] < 0 || labels[3] < 0 || labels[0] == labels[3]) return false;
    if (weighted) {
        if (labels[6] < 0 || labels[6] == labels[0] || labels[6] == labels[3]) return false;
    } else if (labels[6] != -1) return false;
    const auto rows = dal::row_accessor<const std::int32_t>(indices).pull();
    const auto values = dal::row_accessor<const Float>(observations).pull();
    bool seen[7] = {};
    for (int i = 0; i < core_count; ++i) {
        if (labels[i] != labels[(i / 3) * 3]) return false;
        const auto row = rows[i];
        if (row < 0 || row >= core_count || seen[row] || values[i] != points[row]) return false;
        seen[row] = true;
    }
    return true;
}

int main() {
    for (bool weighted : {false, true}) {
        for (bool save_memory : {false, true}) {
            if (!clusters_match<float>(save_memory, weighted) ||
                !clusters_match<double>(save_memory, weighted)) return 1;
        }
    }
    return 0;
}
