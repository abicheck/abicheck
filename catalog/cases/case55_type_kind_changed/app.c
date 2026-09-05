#include <stdio.h>

/* App compiled against v1 struct layout */
typedef struct { int x; int y; } Data;

extern void data_init(Data *d, int x, int y);
extern int data_sum(const Data *d);

int main(void) {
    Data d;
    data_init(&d, 10, 20);
    int sum = data_sum(&d);
    printf("sum = %d\n", sum);

    if (sum != 30) {
        printf("WRONG RESULT: type kind changed (struct -> union)\n");
        return 1;
    }
    return 0;
}
