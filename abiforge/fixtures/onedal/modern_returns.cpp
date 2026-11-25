// SPDX-License-Identifier: Apache-2.0
#include "oneapi/dal/spmd/exceptions.hpp"
#include "oneapi/dal/table/heterogen.hpp"
#include <exception>
namespace dal = oneapi::dal;

bool modern_returns() {
    const auto table = dal::heterogen_table::empty(dal::table_metadata(dal::array<dal::data_type>::full(1, dal::data_type::float32), dal::array<dal::feature_type>::full(1, dal::feature_type::ratio)));
    if (table.get_column_count() != 1 || table.get_row_count()) return false;
    const auto held = [] {
        auto actual = std::make_exception_ptr(137);
        return dal::preview::spmd::error_holder(actual).get_actual();
    }();
    if (!held) return false;
    try { std::rethrow_exception(held); }
    catch (int value) { return value == 137; }
    catch (...) { return false; }
}
