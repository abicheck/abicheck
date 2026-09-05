/* bad.h — struct with default alignment (natural packing). */
#ifndef MYLIB_H
#define MYLIB_H

typedef struct {
    char  tag;      /* offset 0 */
    /* 3 bytes padding */
    int   value;    /* offset 4 */
    char  status;   /* offset 8 */
    /* 3 bytes padding */
} Record;           /* sizeof = 12 */

Record* record_create(char tag, int value, char status);
void record_destroy(Record *r);
int record_get_value(const Record *r);

#endif
