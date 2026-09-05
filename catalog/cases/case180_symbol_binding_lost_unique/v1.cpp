#include "v1.hpp"

extern "C" int get_widget_id() {
    return Registry<Widget>::instance().id;
}
