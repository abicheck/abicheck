#include "v1.h"

extern "C" Widget* make_widget() {
    Widget* w = new Widget();
    w->value = 42;  // Payload::value
    w->extra = 7;
    return w;
}

extern "C" long widget_payload_value(const Widget* w) {
    return w->value;
}
