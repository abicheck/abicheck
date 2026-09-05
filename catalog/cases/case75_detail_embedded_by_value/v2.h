// case75 v2 — detail::table_impl gains a new field.
//
// Because `table` embeds it by value, sizeof(table) grows. Any consumer
// compiled against v1 will mis-allocate, mis-copy, and mis-pass the
// public `table` type.
#pragma once

// See v1.h — no <cstddef> include to avoid GCC 15+ libstdc++ headers
// that castxml's bundled clang can't parse (the ``bf16`` floating-point
// literal in ``bits/c++config.h``).

namespace mylib {
namespace detail {

struct table_impl {
    unsigned long row_count;
    unsigned long column_count;
    unsigned long layout_kind;   // NEW FIELD — leaks via mylib::table
};

} // namespace detail

class table {
public:
    table();
    unsigned long row_count() const;
    unsigned long column_count() const;
    unsigned long layout_kind() const;
private:
    detail::table_impl impl_;
};

extern "C" table* mylib_make_table();
extern "C" void mylib_free_table(table*);

} // namespace mylib
