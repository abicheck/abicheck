/* v1: built with the default global-dynamic TLS access model. A
 * dynamically-loaded (dlopen'd) library using this model can allocate its
 * thread-local storage lazily, at first access, from any thread — so the
 * library remains safely loadable after process startup. */
__thread int counter = 0;

int bump(void) {
    return ++counter;
}
