#ifndef TOKEN_H
#define TOKEN_H
typedef struct token { int kind; const char *text; unsigned len; } token;
int token_next(const char **cursor, token *out);
#endif
