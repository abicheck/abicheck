#include "v2.h"

namespace mylib {

table::table() : impl_{3, 4, 1} {}
unsigned long table::row_count() const { return impl_.row_count; }
unsigned long table::column_count() const { return impl_.column_count; }
unsigned long table::layout_kind() const { return impl_.layout_kind; }

extern "C" table* mylib_make_table() { return new table(); }
extern "C" void mylib_free_table(table* p) { delete p; }

} // namespace mylib
