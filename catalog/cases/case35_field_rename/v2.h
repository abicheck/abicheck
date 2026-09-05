/* case35 v2: Fields renamed */
#ifndef V2_H
#define V2_H

#ifdef __cplusplus
extern "C" {
#endif

struct Point {
    int col;    /* was x */
    int row;    /* was y */
};

struct Point make_point(int a, int b);

#ifdef __cplusplus
}
#endif
#endif
