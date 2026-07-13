abicheck_add_case(case179_cet_protection_weakened
    V1_SOURCES v1.c
    V2_SOURCES v2.c
    V1_COMPILE_OPTIONS -fcf-protection=full
    V2_COMPILE_OPTIONS -fcf-protection=none
    APP_SOURCES app.c
    PLATFORMS linux
)
