#include "corpus.h"

namespace corpus {

Widget::Widget() : origin_{}, extent_{} {}
Widget::~Widget() {}
int Widget::area() const { return origin_.x; }
auto Widget::value() const -> int { return origin_.x; }
Box<int> Widget::make_box() { return Box<int>{}; }

int add(int a, int b) { return a + b; }
double scale(const Point& p, double factor) { return p.y * factor; }
Color mix(Color a, Color b) {
    return static_cast<Color>(static_cast<unsigned>(a) | static_cast<unsigned>(b));
}
Legacy legacy(Legacy l) { return l; }
handle_t open_handle(const char* path, size_type flags) {
    return static_cast<handle_t>(flags) + (path ? 1u : 0u);
}
static detail::Node g_node{nullptr, "head"};
detail::Node* first_node() { return &g_node; }
Box<int> int_box(int fill) {
    Box<int> b{};
    b.items[0] = fill;
    b.count = 1;
    return b;
}
Box<Point, 2> point_box() { return Box<Point, 2>{}; }
static int g_x = 0;
auto& set_x() { return g_x; }
auto get_y() { return 2.5; }
int global_counter = 0;
const Point origin_point{};

}  // namespace corpus
