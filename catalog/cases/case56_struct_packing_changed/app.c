#include <stdio.h>

/* App compiled against v1 layout (natural alignment, sizeof=12) */
typedef struct {
    char  tag;
    int   value;    /* offset 4 */
    char  status;
} Record;

extern Record* record_create(char tag, int value, char status);
extern void record_destroy(Record *r);

int main(void) {
    Record *r = record_create('A', 42, 'X');
    printf("value = %d\n", r->value);
    int ok = (r->value == 42);
    record_destroy(r);

    if (!ok) {
        printf("WRONG RESULT: struct packing/layout changed\n");
        return 1;
    }
    return 0;
}
