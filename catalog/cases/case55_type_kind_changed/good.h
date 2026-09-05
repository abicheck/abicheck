/* good.h — Data changed from struct to union.
   x and y now overlap in memory instead of being sequential. */
#ifndef MYLIB_H
#define MYLIB_H

typedef union {
    int x;
    int y;
} Data;

void data_init(Data *d, int x, int y);
int data_sum(const Data *d);

#endif
