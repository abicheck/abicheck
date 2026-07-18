abicheck_add_case(case61_var_added
    V1_SOURCES old/lib.c
    V2_SOURCES new/lib.c
    V1_HEADERS old/lib.h
    V2_HEADERS new/lib.h
    APP_SOURCES app.c
    PLATFORMS linux macos
)
