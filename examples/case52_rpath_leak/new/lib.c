/* good.c — same library linked with $ORIGIN-relative RUNPATH or no RPATH.
   This is the correct approach for deployment. */
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
