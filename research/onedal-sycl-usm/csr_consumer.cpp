#include <oneapi/dal/table/csr.hpp>
#include <oneapi/dal/table/csr_accessor.hpp>

namespace dal = oneapi::dal;

int main() {
    sycl::queue q;
    const auto shared = sycl::usm::alloc::shared;
    auto data = dal::array<float>::empty(q, 3, shared);
    auto columns = dal::array<std::int64_t>::empty(q, 3, shared);
    auto offsets = dal::array<std::int64_t>::empty(q, 3, shared);
    const float values[] = {4, 7, 2};
    const std::int64_t cols[] = {1, 3, 2}, rows[] = {1, 3, 4};
    auto d = q.memcpy(data.get_mutable_data(), values, sizeof(values));
    auto c = q.memcpy(columns.get_mutable_data(), cols, sizeof(cols));
    auto r = q.memcpy(offsets.get_mutable_data(), rows, sizeof(rows));
    auto table = dal::csr_table::wrap(q, data.get_data(), columns.get_data(), offsets.get_data(),
                                      2, 3, dal::sparse_indexing::one_based, {d, c, r});
    dal::csr_accessor<const float> accessor(table);
    auto [v, i, o] = accessor.pull(q);
    if (v.get_count() != 3 || i.get_count() != 3 || o.get_count() != 3) return 1;
    for (int n = 0; n < 3; ++n) {
        if (v[n] != values[n] || i[n] != cols[n] || o[n] != rows[n]) return 2;
    }
    dal::array<float> slice;
    dal::array<std::int64_t> indices, starts;
    auto [sv, si, so] = accessor.pull(q, slice, indices, starts, {1, 2},
                                      dal::sparse_indexing::zero_based);
    if (slice.get_count() < 1 || indices.get_count() < 1 || starts.get_count() < 2) return 3;
    return !(sv[0] == 2 && si[0] == 1 && so[0] == 0 && so[1] == 1);
}
