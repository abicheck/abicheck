#include "v1.h"

namespace ui {
Widget *make_widget(int id) { return new Widget{id, 1}; }
}  // namespace ui
