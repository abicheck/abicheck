// SPDX-License-Identifier: Apache-2.0
#include <cstdint>
#include <oneapi/dal/algo/hdbscan.hpp>
#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/row_accessor.hpp>

namespace dal = oneapi::dal;
using result_type = dal::hdbscan::compute_result<>;

dal::table make_table(std::int32_t value) {
    auto data = dal::array<std::int32_t>::empty(1);
    data.get_mutable_data()[0] = value;
    return dal::homogen_table::wrap(data, 1, 1);
}

int main() {
    namespace options = dal::hdbscan::result_options;
    const auto all = options::responses | options::core_flags | options::core_observation_indices |
                     options::core_observations | options::cluster_centers | options::medoid_centers;
    result_type retained;
    {
        result_type source;
        source.set_result_options(all);
        source.set_cluster_count(2);
        source.set_responses(make_table(1));
        source.set_core_flags(make_table(2));
        source.set_core_observation_indices(make_table(3));
        source.set_core_observations(make_table(4));
        source.set_cluster_centers(make_table(5));
        source.set_medoid_centers(make_table(6));
        retained = source;
    }
    if (retained.get_cluster_count() != 2 || retained.get_result_options() != all) return 1;
    int expected = 0;
    for (auto getter : {&result_type::get_responses, &result_type::get_core_flags,
                        &result_type::get_core_observation_indices, &result_type::get_core_observations,
                        &result_type::get_cluster_centers, &result_type::get_medoid_centers}) {
        const auto& table = (retained.*getter)();
        if (table.get_row_count() != 1 || table.get_column_count() != 1) return 2;
        if (dal::row_accessor<const std::int32_t>(table).pull()[0] != ++expected) return 3;
    }
    return 0;
}
