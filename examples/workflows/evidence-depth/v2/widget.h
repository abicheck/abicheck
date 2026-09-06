#pragma once
#define BUFFER_LIMIT 32

struct Widget {
    int a;
    int extra;
    int b;
};

int widget_get_b(struct Widget *w);
int widget_get_limit(void);
