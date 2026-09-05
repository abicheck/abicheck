/* good.h — struct with pragma pack(1), eliminating all padding. */
#ifndef MYLIB_H
#define MYLIB_H

#pragma pack(push, 1)
typedef struct {
    char  tag;      /* offset 0 */
    int   value;    /* offset 1 (no padding!) */
    char  status;   /* offset 5 */
} Record;           /* sizeof = 6 */
#pragma pack(pop)

Record* record_create(char tag, int value, char status);
void record_destroy(Record *r);
int record_get_value(const Record *r);

#endif
