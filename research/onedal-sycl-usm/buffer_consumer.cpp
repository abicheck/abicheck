#include <oneapi/dal/chunked_array.hpp>
#include <oneapi/dal/table/column_accessor.hpp>
#include <oneapi/dal/table/row_accessor.hpp>
#include <algorithm>

namespace dal = oneapi::dal;

int main() {
    sycl::queue q;
    const auto shared = sycl::usm::alloc::shared;
    auto matrix = dal::array<float>::zeros(q, 6, shared);
    auto row = dal::array<float>::empty(q, 3, shared);
    auto column = dal::array<float>::empty(q, 2, shared);
    const float a[] = {10, 11, 12}, b[] = {21, 22};
    auto a_ready = q.memcpy(row.get_mutable_data(), a, sizeof(a));
    auto b_ready = q.memcpy(column.get_mutable_data(), b, sizeof(b));
    dal::chunked_array<float> chunks(row, column);
    auto flat = chunks.flatten(q, shared, {a_ready, b_ready});
    auto pointers = chunks.get_data(q, shared, {a_ready, b_ready});
    const float joined[] = {10, 11, 12, 21, 22};
    if (flat.get_count() != 5 || !std::equal(joined, joined + 5, flat.get_data())) return 1;
    if (pointers.get_count() != 2 || pointers[0] != row.get_data() ||
        pointers[1] != column.get_data()) return 2;

    dal::detail::homogen_table_builder builder;
    builder.reset(matrix, 2, 3);
    dal::row_accessor<float>(builder).push(q, row, {1, 2});
    dal::column_accessor<float>(builder).push(q, column, 1);
    auto table = builder.build();
    auto rows = dal::array<float>::empty(q, 6, shared);
    auto cols = dal::array<float>::empty(q, 2, shared);
    auto rp = dal::row_accessor<const float>(table).pull(q, rows);
    auto cp = dal::column_accessor<const float>(table).pull(q, cols, 1);
    q.wait_and_throw();
    const float expected[] = {0, 21, 0, 10, 22, 12};
    if (rows.get_count() < 6 || cols.get_count() < 2) return 3;
    return !(std::equal(expected, expected + 6, rp) && std::equal(b, b + 2, cp));
}
