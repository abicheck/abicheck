# case134: RELRO weakened — v1 is linked with full RELRO (-z relro -z now),
# v2 drops it (-z norelro). Identical ABI surface; the GNU_RELRO program
# header disappears, removing the read-only-after-reloc hardening.
abicheck_add_case(case134_relro_weakened
    V1_SOURCES v1.c
    V1_LINK_OPTIONS -Wl,-z,relro -Wl,-z,now
    V2_SOURCES v2.c
    V2_LINK_OPTIONS -Wl,-z,norelro
    APP_SOURCES app.c
    PLATFORMS linux
)
