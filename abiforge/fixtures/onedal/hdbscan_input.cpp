// SPDX-License-Identifier: Apache-2.0
#include <cstdint>
#include <oneapi/dal/algo/hdbscan.hpp>
#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/row_accessor.hpp>

namespace dal = oneapi::dal;

dal::table tracked_table(std::int32_t value, int& releases) {
    dal::array<std::int32_t> data(new std::int32_t[]{value}, 1, [&releases](std::int32_t* ptr) {
        ++releases;
        delete[] ptr;
    });
    return dal::homogen_table::wrap(data, 1, 1);
}

int main() {
    int original_releases = 0;
    int replacement_releases = 0;
    {
        dal::hdbscan::compute_input<> input(tracked_table(17, original_releases));
        if (original_releases != 0) return 1;
        if (dal::row_accessor<const std::int32_t>(input.get_data()).pull()[0] != 17) return 2;
        auto& updated = input.set_data(tracked_table(31, replacement_releases));
        if (&updated != &input || original_releases != 1 || replacement_releases != 0) return 3;
        if (dal::row_accessor<const std::int32_t>(input.get_data()).pull()[0] != 31) return 4;
    }
    return original_releases == 1 && replacement_releases == 1 ? 0 : 5;
}
