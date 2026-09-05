/* good.h — reserved fields put into use with meaningful names.
   Layout is identical — same offsets, same sizes. */
#ifndef MYLIB_H
#define MYLIB_H

typedef struct {
    int version;
    int priority;       /* was __reserved1 */
    int max_retries;    /* was __reserved2 */
    int flags;
} Config;

Config* config_create(void);
void config_destroy(Config *c);
int config_get_flags(const Config *c);

#endif
