/* Frozen container corpus (tests/fixtures/containers): the second library of
 * the two-member ELF release, so the G38/G40 captures carry a release public
 * surface (one member never does). Plain C: no mangling, an opaque handle, a
 * versionless struct and an enum. Changing this file or extra.c invalidates
 * every frozen ELF container (see ../manifest.json). */
#pragma once

#ifdef __cplusplus
extern "C" {
#endif

typedef struct extra_ctx extra_ctx;

enum extra_mode { EXTRA_READ = 1, EXTRA_WRITE = 2 };

struct extra_stat {
    unsigned long bytes;
    int mode;
};

extra_ctx *extra_open(const char *name, enum extra_mode mode);
int extra_stat_of(const extra_ctx *ctx, struct extra_stat *out);
void extra_close(extra_ctx *ctx);

extern int extra_version;

#ifdef __cplusplus
}
#endif
