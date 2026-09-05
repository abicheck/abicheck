// v1: get_id() carries an Itanium abi_tag ("cxx11"); the tag is part of the
// mangled name. Self-contained (no header) so the snapshot is taken from the
// compiled library's DWARF rather than a castxml header parse.

[[gnu::abi_tag("cxx11")]] int get_id();

struct Widget {
    int value;
};

int get_id() { return 7; }

int widget_value(const Widget *w) { return w->value; }
