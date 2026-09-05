#include <stdio.h>

/* Using the bad (unprefixed) API to demonstrate the collision risk. */
extern int init(void);
extern int process(int data);
extern void cleanup(void);
extern int status(void);

int main(void) {
    init();
    printf("status() = %d\n", status());
    printf("process(21) = %d\n", process(21));
    cleanup();
    printf("status() = %d\n", status());
    return 0;
}
