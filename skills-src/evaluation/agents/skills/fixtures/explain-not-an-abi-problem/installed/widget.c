#include "widget.h"
int widget_create(int size) { return size * 2; }
int widget_resize(int handle, int size) { return size < 0 ? -1 : handle + size; }
