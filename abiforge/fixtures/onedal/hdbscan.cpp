// SPDX-License-Identifier: Apache-2.0
#include <cstdint>
#include <initializer_list>
#include <type_traits>
#include <oneapi/dal/algo/hdbscan.hpp>
#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/row_accessor.hpp>

namespace dal = oneapi::dal;

template <typename Float, typename Method>
bool clusters_match() {
    // Two unit squares, separated by a gap of 99 along the x-axis.
    const Float points[] = {0, 0, 0, 1, 1, 0, 1, 1, 100, 0, 100, 1, 101, 0, 101, 1};
    dal::hdbscan::descriptor<Float, Method> descriptor(3, 2);
    namespace options = dal::hdbscan::result_options;
    descriptor.set_store_centers(dal::hdbscan::store_centers_method::both);
    descriptor.set_result_options(options::responses | options::core_flags |
                                  options::core_observation_indices | options::core_observations |
                                  options::cluster_centers | options::medoid_centers);
    const auto result = dal::compute(descriptor, dal::homogen_table::wrap(points, 8, 2));
    const auto& responses = result.get_responses();
    if (result.get_cluster_count() != 2 || responses.get_row_count() != 8 ||
        responses.get_column_count() != 1) {
        return false;
    }
    const auto labels = dal::row_accessor<const std::int32_t>(responses).pull();
    if (labels[0] < 0 || labels[4] < 0 || labels[0] == labels[4]) {
        return false;
    }
    for (int i = 1; i < 4; ++i) {
        if (labels[i] != labels[0] || labels[i + 4] != labels[4]) {
            return false;
        }
    }
    const auto& centers = result.get_cluster_centers();
    const auto& medoids = result.get_medoid_centers();
    if (centers.get_row_count() != 2 || centers.get_column_count() != 2 ||
        medoids.get_row_count() != 2 || medoids.get_column_count() != 2) {
        return false;
    }
    const auto means = dal::row_accessor<const Float>(centers).pull();
    const auto representatives = dal::row_accessor<const Float>(medoids).pull();
    for (int group = 0; group < 2; ++group) {
        const auto label = labels[4 * group];
        if (label > 1) return false;
        const int offset = 2 * label;
        const Float x = 100 * group;
        if (means[offset] != x + 0.5 || means[offset + 1] != 0.5) return false;
        // All four corners tie as medoids of their square.
        if ((representatives[offset] != x && representatives[offset] != x + 1) ||
            (representatives[offset + 1] != 0 && representatives[offset + 1] != 1)) return false;
    }
    const auto& flags = result.get_core_flags();
    const auto& indices = result.get_core_observation_indices();
    const auto& observations = result.get_core_observations();
    // The pinned 2026.2 CPU build returns empty tables for all three core options.
    const auto empty = [](const dal::table& table) {
        return table.get_row_count() == 0 && table.get_column_count() == 0;
    };
    if (!empty(flags) || !empty(indices) || !empty(observations)) return false;

    descriptor.set_result_options(options::responses);
    const auto labels_only = dal::compute(descriptor, dal::homogen_table::wrap(points, 8, 2));
    if (labels_only.get_result_options() != options::responses ||
        labels_only.get_responses().get_row_count() != 8) return false;
    using result_type = std::remove_const_t<decltype(labels_only)>;
    for (auto getter : {&result_type::get_cluster_centers, &result_type::get_medoid_centers,
                        &result_type::get_core_flags, &result_type::get_core_observation_indices,
                        &result_type::get_core_observations}) {
        try {
            (labels_only.*getter)();
            return false;
        }
        catch (const dal::domain_error&) {
            // Access to a disabled result must throw across the shared-library boundary.
        }
    }
    return true;
}

template <typename Method>
bool check_precisions() {
    return clusters_match<float, Method>() && clusters_match<double, Method>();
}

int main() {
    namespace method = dal::hdbscan::method;
    return check_precisions<method::brute_force>() && check_precisions<method::kd_tree>() &&
           check_precisions<method::ball_tree>() ? 0 : 1;
}
