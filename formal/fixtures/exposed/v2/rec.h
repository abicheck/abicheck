/* v2 public header: a field inserted before b -> b moves 4 -> 8 */
#ifndef REC_H
#define REC_H
struct rec { int a; int x; int b; };
struct rec *rec_new(void);
int rec_get_b(const struct rec *r);
#endif
