// v2: the abi_tag on get_id() was removed, so its mangled name changes and the
// old symbol disappears. Self-contained (no header).

int get_id();   // abi_tag removed

struct Widget {
    int value;
};

int get_id() { return 7; }

int widget_value(const Widget *w) { return w->value; }
