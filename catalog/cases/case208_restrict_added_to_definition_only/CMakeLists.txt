abicheck_add_case(case208_restrict_added_to_definition_only
    V1_SOURCES v1.c
    V2_SOURCES v2.c
    V1_HEADERS v1.h
    V2_HEADERS v2.h
    # Both libraries are optimised regardless of the build type: the
    # restrict promise only changes behaviour once the optimizer acts on it.
    V1_COMPILE_OPTIONS -O3
    V2_COMPILE_OPTIONS -O3
    APP_SOURCES app.c
    PLATFORMS linux
)
