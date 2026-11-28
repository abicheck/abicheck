// SPDX-License-Identifier: Apache-2.0
#include <string_view>
#include <oneapi/dal/algo/hdbscan.hpp>
#include <oneapi/dal/detail/error_messages.hpp>

namespace hdb = oneapi::dal::hdbscan;

template <typename Operation>
bool rejects_value(const char* expected, Operation operation) {
    try {
        operation();
    }
    catch (const oneapi::dal::domain_error& error) {
        return std::string_view(error.what()) == expected;
    }
    return false;
}

int main() {
    hdb::descriptor<> descriptor(9, 4);
    const auto options = hdb::result_options::responses | hdb::result_options::cluster_centers;
    descriptor.set_metric(hdb::distance_metric::minkowski);
    descriptor.set_degree(3.5);
    descriptor.set_cluster_selection(hdb::cluster_selection_method::leaf);
    descriptor.set_allow_single_cluster(true);
    descriptor.set_cluster_selection_epsilon(0.25);
    descriptor.set_max_cluster_size(31);
    descriptor.set_alpha(1.5);
    descriptor.set_store_centers(hdb::store_centers_method::centroid);
    descriptor.set_leaf_size(17);
    descriptor.set_distance_block_size(64);
    descriptor.set_result_options(options);

    using messages = oneapi::dal::detail::error_messages;
    const char* gpu_diagnostic = messages::hdbscan_kd_tree_method_is_not_implemented_for_gpu();
    if (!gpu_diagnostic || std::string_view(gpu_diagnostic) !=
        "HDBSCAN k-d tree method is not implemented for GPU") return 19;
    if (!rejects_value(messages::hdbscan_min_cluster_size_lt_two(),
                       [&] { descriptor.set_min_cluster_size(1); })) return 11;
    if (!rejects_value(messages::hdbscan_min_samples_lt_one(),
                       [&] { descriptor.set_min_samples(0); })) return 12;
    if (!rejects_value(messages::hdbscan_minkowski_degree_leq_zero(),
                       [&] { descriptor.set_degree(0); })) return 13;
    if (!rejects_value(messages::hdbscan_cluster_selection_epsilon_lt_zero(),
                       [&] { descriptor.set_cluster_selection_epsilon(-1); })) return 14;
    if (!rejects_value(messages::hdbscan_max_cluster_size_lt_zero(),
                       [&] { descriptor.set_max_cluster_size(-1); })) return 15;
    if (!rejects_value(messages::hdbscan_alpha_leq_zero(),
                       [&] { descriptor.set_alpha(0); })) return 16;
    if (!rejects_value(messages::hdbscan_leaf_size_lt_one(),
                       [&] { descriptor.set_leaf_size(0); })) return 17;
    if (!rejects_value(messages::hdbscan_distance_block_size_lt_zero(),
                       [&] { descriptor.set_distance_block_size(-1); })) return 18;

    if (descriptor.get_min_cluster_size() != 9 || descriptor.get_min_samples() != 4) return 1;
    if (descriptor.get_metric() != hdb::distance_metric::minkowski || descriptor.get_degree() != 3.5) return 2;
    if (descriptor.get_cluster_selection() != hdb::cluster_selection_method::leaf ||
        !descriptor.get_allow_single_cluster() || descriptor.get_cluster_selection_epsilon() != 0.25) return 3;
    if (descriptor.get_max_cluster_size() != 31 || descriptor.get_alpha() != 1.5) return 4;
    if (descriptor.get_store_centers() != hdb::store_centers_method::centroid ||
        descriptor.get_result_options() != options) return 5;
    if (descriptor.get_leaf_size() != 17 || descriptor.get_distance_block_size() != 64) return 6;
    return 0;
}
