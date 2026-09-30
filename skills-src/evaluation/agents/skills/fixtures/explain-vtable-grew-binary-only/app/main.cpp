#include "widget.h"
struct Rect : widget::Shape {
    int w, h;
    Rect(int w_, int h_) : w(w_), h(h_) {}
    int area() const override { return w * h; }
};
int main() {
    Rect r(3, 4);
    widget::describe(r);
    return 0;
}
