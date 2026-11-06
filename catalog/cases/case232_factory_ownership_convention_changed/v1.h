#ifndef CASE232_H
#define CASE232_H

namespace ui {
struct Widget {
    int id;
    int value;
};

/* Creates a widget.
 * Contract (documentation only): every call returns a new object that the
 * caller owns and must delete. */
Widget *make_widget(int id);
}  // namespace ui

#endif
