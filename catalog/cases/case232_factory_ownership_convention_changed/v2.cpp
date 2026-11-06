#include "v2.h"

namespace ui {
/* Same declaration and symbol. The library now caches one widget per id and
 * keeps ownership: two calls with one id return the same shared object, and
 * a caller that deletes it frees memory the library still references. */
Widget *make_widget(int id) {
    static Widget *cache[64] = {};
    static int used = 0;
    for (int i = 0; i < used; ++i)
        if (cache[i]->id == id) return cache[i];
    Widget *w = new Widget{id, 1};
    if (used < 64) cache[used++] = w;  // beyond 64 ids, fall back to a fresh object
    return w;
}
}  // namespace ui
