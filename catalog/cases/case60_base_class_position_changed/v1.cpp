/* v1.cpp — Widget inherits Drawable first, then Clickable.
   Memory layout: [Drawable subobject][Clickable subobject][Widget fields] */
#include <cstdio>

struct Drawable {
    int draw_x;
    int draw_y;
    virtual void draw();
    virtual ~Drawable();
};

struct Clickable {
    int click_zone;
    virtual void on_click();
    virtual ~Clickable();
};

/* v1: Drawable first, Clickable second */
struct Widget : public Drawable, public Clickable {
    int widget_id;
};

/* Out-of-line (key-function) definitions: the library is the one place that
   emits these members and the two vtables, at every optimisation level, so
   a consumer compiled against the same declarations links in Release too. */
void Drawable::draw() { printf("draw at (%d,%d)\n", draw_x, draw_y); }
Drawable::~Drawable() = default;
void Clickable::on_click() { printf("clicked zone %d\n", click_zone); }
Clickable::~Clickable() = default;

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
