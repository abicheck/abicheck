/* v2: identical source, but built with -ftls-model=initial-exec. The
 * initial-exec model requires the dynamic linker to assign this library's
 * TLS block a fixed slot in the process's *static* TLS surplus at load
 * time — a slot budget sized for the libraries present at startup. The
 * linker records this requirement as DF_STATIC_TLS in the dynamic
 * section. */
__thread int counter = 0;

int bump(void) {
    return ++counter;
}
