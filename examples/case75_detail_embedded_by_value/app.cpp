// Consumer never references detail::table_impl — only mylib::table.
// A layout change to the "internal" impl still corrupts this program.
#include "v1.h"
#include <cstdio>

int main() {
    using namespace mylib;
    table t;
    std::printf("rows=%lu cols=%lu (expect 3 4)\n",
                t.row_count(), t.column_count());
    return 0;
}
