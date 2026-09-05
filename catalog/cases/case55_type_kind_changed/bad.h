/* bad.h — Data is a struct. */
#ifndef MYLIB_H
#define MYLIB_H

typedef struct {
    int x;
    int y;
} Data;

void data_init(Data *d, int x, int y);
int data_sum(const Data *d);

#endif
