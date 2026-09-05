# case53: Namespace pollution — v1 exports generic names (init, process, cleanup)
# that will collide with other libraries. v2 uses proper "mylib_" prefix.
# The ABI surface changes (symbols renamed), but the issue is the unprefixed names.
abicheck_add_case(case53_namespace_pollution
    V1_SOURCES bad.c
    V2_SOURCES good.c
    APP_SOURCES app.c
    PLATFORMS linux
)
