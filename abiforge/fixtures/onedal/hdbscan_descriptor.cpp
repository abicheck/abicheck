// SPDX-License-Identifier: Apache-2.0
#include <oneapi/dal/algo/hdbscan.hpp>

namespace hdb = oneapi::dal::hdbscan;

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
