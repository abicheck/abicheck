extern int get_version(void);
extern int get_extra(void);

int daemon_run(void) {
    return get_version() + get_extra();
}
