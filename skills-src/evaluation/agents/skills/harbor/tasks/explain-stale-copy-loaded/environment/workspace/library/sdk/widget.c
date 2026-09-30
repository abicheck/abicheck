#include "widget.h"
int widget_create(int size) { return size * 2; }
int widget_resize(int handle, int size) { return handle + size; }
