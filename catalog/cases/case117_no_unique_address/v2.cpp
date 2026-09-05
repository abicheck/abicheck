// v2: EmptyPolicy stored with [[no_unique_address]] — overlaps with the
//     next member, shrinking Widget. Self-contained (no header).

struct EmptyPolicy {};

struct Widget {
    [[no_unique_address]] EmptyPolicy policy;  // overlaps next member
    long value;
};

int widget_value(const Widget *w) { return (int)w->value; }
