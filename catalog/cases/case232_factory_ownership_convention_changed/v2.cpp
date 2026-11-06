#include "v2.h"

namespace ui {
namespace {
struct Node {
    Widget widget;
    Node *next;
};
Node *cache_head = nullptr;  // library-owned, never freed
}  // namespace

/* Same declaration and symbol. The library now caches one widget per id and
 * keeps ownership: two calls with one id return the same shared object, and
 * a caller that deletes it frees memory the library still references. */
Widget *make_widget(int id) {
    for (Node *n = cache_head; n != nullptr; n = n->next)
        if (n->widget.id == id) return &n->widget;
    cache_head = new Node{Widget{id, 1}, cache_head};
    return &cache_head->widget;
}
}  // namespace ui
