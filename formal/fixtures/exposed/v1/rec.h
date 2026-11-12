/* v1 public header: layout a@0 b@4 */
#ifndef REC_H
#define REC_H
struct rec { int a; int b; };
struct rec *rec_new(void);
int rec_get_b(const struct rec *r);
#endif
