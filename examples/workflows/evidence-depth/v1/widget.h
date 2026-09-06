#pragma once
#define BUFFER_LIMIT 16

struct Widget {
    int a;
    int b;
};

int widget_get_b(struct Widget *w);
int widget_get_limit(void);
