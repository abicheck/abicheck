/* bad.c — library that will be linked with a hardcoded build-directory RPATH.
   The code is identical; the bad practice is in the link flags. */
int encode(const char *input) {
    int hash = 0;
    while (*input) {
        hash = hash * 31 + *input;
        input++;
    }
    return hash;
}

int decode(int code) {
    return code ^ 0xDEAD;
}
