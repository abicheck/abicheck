#ifndef PARSER_H
#define PARSER_H

#ifdef __cplusplus
extern "C" {
#endif

/* C linkage: symbol name is "parse_config" in the dynamic symbol table */
int parse_config(const char *path);
int validate_config(const char *path);

#ifdef __cplusplus
}
#endif

#endif
