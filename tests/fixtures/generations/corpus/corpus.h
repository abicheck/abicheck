// Frozen extractor-generation corpus (bug class
// storage.cross_generation_spelling_drift). One small library whose public
// header stores a type spelling at each position the extractors write one:
// undeduced auto returns, templates, typedefs, namespaces, enums, fields,
// parameters and variables. Changing this file or corpus.cpp invalidates
// every frozen snapshot under ../gen*/ (see ../manifest.json).
#pragma once


namespace corpus {

enum class Color : unsigned char { Red = 1, Green = 2, Blue = 4 };
enum Legacy { LEGACY_A, LEGACY_B = 7 };

typedef unsigned long handle_t;
using size_type = decltype(sizeof(0));

template <typename T, int N = 4>
struct Box {
    T items[N];
    int count;
};

struct Point {
    int x;
    double y;
    Color color;
    handle_t handle;
};

class Widget {
public:
    Widget();
    virtual ~Widget();
    virtual int area() const;
    auto value() const -> int;
    static Box<int> make_box();
private:
    Point origin_;
    Box<double, 2> extent_;
};

namespace detail {
struct Node {
    Node* next;
    const char* label;
};
}  // namespace detail

int add(int a, int b);
double scale(const Point& p, double factor);
Color mix(Color a, Color b);
Legacy legacy(Legacy l);
handle_t open_handle(const char* path, size_type flags);
detail::Node* first_node();
Box<int> int_box(int fill);
Box<Point, 2> point_box();
// Undeduced in every TU that sees only this header: castxml spelled the
// return 'AutoType&' before extractor generation 6 and 'auto&' since.
auto& set_x();
auto get_y();
extern int global_counter;
extern const Point origin_point;

}  // namespace corpus
