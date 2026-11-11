/* public header, both versions: forward declaration only */
#ifndef OREC_H
#define OREC_H
struct orec;
struct orec *orec_new(void);
int orec_get_b(const struct orec *r);
#endif
