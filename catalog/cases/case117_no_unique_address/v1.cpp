// v1: EmptyPolicy stored as an ordinary member — occupies a full byte
//     (plus padding) inside Widget. Self-contained (no header) so the
//     snapshot is taken from the compiled library's DWARF.

struct EmptyPolicy {};

struct Widget {
    EmptyPolicy policy;   // ordinary member: takes space
    long value;
};

int widget_value(const Widget *w) { return (int)w->value; }
