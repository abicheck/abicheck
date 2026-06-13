#include "v2.h"

extern "C" Widget* make_widget() {
    Widget* w = new Widget();
    w->state = 0;   // Tag::state (new in v2)
    w->value = 42;  // Payload::value — now at offset 8
    w->extra = 7;
    return w;
}

extern "C" long widget_payload_value(const Widget* w) {
    return w->value;
}
