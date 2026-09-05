#include <cstdio>

/* App is compiled with v1 layout assumptions:
 *   Widget : Drawable, Clickable
 */
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

struct Widget : public Drawable, public Clickable {
    int widget_id;
};

extern "C" {
    int widget_get_id(Widget *w);
    void widget_draw(Widget *w);
    void widget_click(Widget *w);
}

int main() {
    Widget w{};
    w.draw_x = 10;
    w.draw_y = 20;
    w.click_zone = 5;
    w.widget_id = 42;

    widget_draw(&w);
    widget_click(&w);
    int id = widget_get_id(&w);

    std::printf("id = %d\n", id);
    std::printf("expected id = 42\n");

    if (id != 42) {
        std::printf("CORRUPTION: base-class order changed, subobject offsets mismatch\n");
        return 1;
    }
    return 0;
}
