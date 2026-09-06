#pragma once

typedef struct {
    int r, g, b;
} RGB;

/* to_rgb() was renamed here -- the approved break this workflow suppresses. */
RGB convert_to_rgb(int hex);
