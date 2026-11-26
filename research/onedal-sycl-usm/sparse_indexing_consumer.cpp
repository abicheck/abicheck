// SPDX-License-Identifier: Apache-2.0
#include <oneapi/dal/table/csr.hpp>
#include <oneapi/dal/table/csr_accessor.hpp>
#include <algorithm>
#include "sparse_indexing_request.hpp"

namespace dal = oneapi::dal;


int main() {
    const float values[] = {9, -2, 5};
    const std::int64_t columns[] = {1, 2, 4}, offsets[] = {1, 2, 2, 4};
    auto table = dal::csr_table::wrap(values, columns, offsets, 3, 4,
                                      dal::sparse_indexing::one_based);
    sparse_request request{7, dal::sparse_indexing::one_based};
    request_zero_based(&request);
    if (request.tag != 7) return 3;
    dal::csr_accessor<const float> accessor(table);
#ifdef ONEDAL_DATA_PARALLEL
    sycl::queue q;
    auto [v, c, o] = accessor.pull(q, {1, 3}, request.indexing);
    q.wait_and_throw();
#else
    auto [v, c, o] = accessor.pull({1, 3}, request.indexing);
#endif
    if (v.get_count() != 2 || c.get_count() != 2 || o.get_count() != 3 ||
        v[0] != -2 || v[1] != 5) return 1;
    const std::int64_t zero_columns[] = {1, 3}, zero_offsets[] = {0, 0, 2};
    const std::int64_t one_columns[] = {2, 4}, one_offsets[] = {1, 1, 3};
    if (std::equal(zero_columns, zero_columns + 2, c.get_data()) &&
        std::equal(zero_offsets, zero_offsets + 3, o.get_data())) return 0;
    if (std::equal(one_columns, one_columns + 2, c.get_data()) &&
        std::equal(one_offsets, one_offsets + 3, o.get_data())) return 42;
    return 2;
}
