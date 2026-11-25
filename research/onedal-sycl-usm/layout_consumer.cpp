// SPDX-License-Identifier: Apache-2.0
#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/row_accessor.hpp>
#include <algorithm>

namespace dal = oneapi::dal;
extern "C" dal::data_layout column_layout();

int main() {
    const float columns[] = {10, -20, 30, 11, -21, 31};
#ifdef ONEDAL_DATA_PARALLEL
    sycl::queue q;
    auto storage = dal::array<float>::empty(q, 6, sycl::usm::alloc::shared);
    auto ready = q.memcpy(storage.get_mutable_data(), columns, sizeof(columns));
    auto table = dal::homogen_table::wrap(q, storage.get_data(), 3, 2, {ready}, column_layout());
    auto slice = dal::row_accessor<const std::int32_t>(table).pull(q, {1, 3});
    q.wait_and_throw();
#else
    auto table = dal::homogen_table::wrap(columns, 3, 2, column_layout());
    auto slice = dal::row_accessor<const std::int32_t>(table).pull({1, 3});
#endif
    if (slice.get_count() != 4) return 1;
    const int expected[] = {-20, -21, 30, 31};
    const int swapped[] = {30, 11, -21, 31};
    if (std::equal(expected, expected + 4, slice.get_data())) return 0;
    return std::equal(swapped, swapped + 4, slice.get_data()) ? 42 : 2;
}
