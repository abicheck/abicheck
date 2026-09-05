// case75 v1 — public class embeds an internal detail:: type *by value*.
//
// Anti-pattern variant of the PIMPL idiom: the implementation type
// `detail::table_impl` is held inline (no pointer indirection), so its
// size and layout flow directly into the public class.
//
// This is intentionally less common than pointer-pimpl, but it shows up
// regularly when a small implementation struct is embedded for performance
// reasons (oneDAL `dal::array` small-buffer optimisations are a real
// example of layout-coupled detail types).
#pragma once

// NOTE: intentionally not including <cstddef> — castxml's bundled clang
// chokes on GCC 15+ libstdc++ headers ("invalid suffix 'bf16'" in
// bits/c++config.h). We use ``unsigned long`` directly instead, which
// is wide enough for the dimensions stored here on every supported
// platform and keeps this header free of system-include dependencies.

namespace mylib {
namespace detail {

// "Internal" implementation type, embedded by value below.
struct table_impl {
    unsigned long row_count;
    unsigned long column_count;
};

} // namespace detail

class table {
public:
    table();
    unsigned long row_count() const;
    unsigned long column_count() const;
private:
    detail::table_impl impl_;
};

extern "C" table* mylib_make_table();
extern "C" void mylib_free_table(table*);

} // namespace mylib
