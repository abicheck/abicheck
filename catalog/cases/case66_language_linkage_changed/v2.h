#ifndef PARSER_H
#define PARSER_H

/* extern "C" removed — functions now use C++ linkage.
   Symbol names are mangled: _Z12parse_configPKc instead of parse_config.
   C consumers and pre-built C++ binaries that expect the unmangled name
   will get "undefined symbol" at load time. */

int parse_config(const char *path);
int validate_config(const char *path);

#endif
