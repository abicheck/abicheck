enum widget_kind { WIDGET_PLAIN, WIDGET_FRAMED };
struct widget {
    enum widget_kind kind;
    short width;
    short height;
};
int widget_area(const struct widget *w);
