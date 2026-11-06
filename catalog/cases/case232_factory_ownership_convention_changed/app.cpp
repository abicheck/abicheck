/* Witness: an unchanged old consumer creates two widgets for one id, edits
   one, and expects the other to be independent -- as v1's "new object per
   call, caller owns it" contract promises.
     v1: b->value stays 1                   -> exit 0 (both deleted)
     v2: a == b, so b->value becomes 5      -> exit 1
   v2 returns before deleting: deleting the library-owned object would be a
   double free / invalid free, an even worse symptom of the same break. */
#include "v1.h"

#include <cstdio>

int main() {
    ui::Widget *a = ui::make_widget(7);
    ui::Widget *b = ui::make_widget(7);
    a->value = 5;
    std::printf("a=%p b=%p b->value=%d\n", static_cast<void *>(a),
                static_cast<void *>(b), b->value);
    if (a == b || b->value != 1) {
        std::printf("WRONG RESULT: widgets are shared, not caller-owned\n");
        return 1;
    }
    delete a;
    delete b;
    return 0;
}
