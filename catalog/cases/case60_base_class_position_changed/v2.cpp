/* v2.cpp — Widget inherits Clickable first, then Drawable.
   Memory layout: [Clickable subobject][Drawable subobject][Widget fields]
   The base class order swap changes all subobject offsets. */
#include <cstdio>

struct Drawable {
    int draw_x;
    int draw_y;
    virtual void draw() { printf("draw at (%d,%d)\n", draw_x, draw_y); }
    virtual ~Drawable() = default;
};

struct Clickable {
    int click_zone;
    virtual void on_click() { printf("clicked zone %d\n", click_zone); }
    virtual ~Clickable() = default;
};

/* v2: Clickable first, Drawable second — swapped! */
struct Widget : public Clickable, public Drawable {
    int widget_id;
};

extern "C" {
    Widget* widget_create(int id) {
        Widget *w = new Widget();
        w->draw_x = 10;
        w->draw_y = 20;
        w->click_zone = 5;
        w->widget_id = id;
        return w;
    }
    void widget_destroy(Widget *w) { delete w; }
    int widget_get_id(Widget *w) { return w->widget_id; }
    void widget_draw(Widget *w) { w->draw(); }
    void widget_click(Widget *w) { w->on_click(); }
}
