#include "token.h"
int token_next(const char **cursor, token *out) { out->kind = **cursor ? 1 : 0; out->text = *cursor; out->len = 1; if (**cursor) (*cursor)++; return out->kind; }
