#include "v2.h"

namespace ui {
/* Same declaration and symbol. The library now caches one widget per id and
 * keeps ownership: two calls with one id return the same shared object, and
 * a caller that deletes it frees memory the library still references. */
Widget *make_widget(int id) {
    static Widget *cache[64] = {};
    Widget *&slot = cache[static_cast<unsigned>(id) % 64];
    if (slot == nullptr) slot = new Widget{id, 1};
    return slot;
}
}  // namespace ui
