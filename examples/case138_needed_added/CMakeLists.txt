# case138: DT_NEEDED added — v2 force-links libm (-Wl,--no-as-needed -lm)
# even though no math symbol is used, adding a runtime dependency. Identical
# exported ABI surface; the library now drags in an extra shared object.
abicheck_add_case(case138_needed_added
    V1_SOURCES v1.c
    V2_SOURCES v2.c
    V2_LINK_OPTIONS -Wl,--no-as-needed -lm
    APP_SOURCES app.c
    PLATFORMS linux
)
