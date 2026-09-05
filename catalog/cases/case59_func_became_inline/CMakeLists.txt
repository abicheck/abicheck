abicheck_add_case(case59_func_became_inline
    V1_SOURCES bad.c
    V2_SOURCES good.c
    V1_HEADERS bad.h
    V2_HEADERS good.h
    APP_SOURCES app.c
    PLATFORMS linux macos
)
