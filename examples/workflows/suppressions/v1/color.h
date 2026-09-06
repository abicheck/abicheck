#pragma once

typedef struct {
    int r, g, b;
} RGB;

RGB to_rgb(int hex);
