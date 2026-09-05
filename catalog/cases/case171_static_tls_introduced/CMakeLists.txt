abicheck_add_case(case171_static_tls_introduced
    V1_SOURCES v1.c
    V2_SOURCES v2.c
    V2_COMPILE_OPTIONS -ftls-model=initial-exec
    APP_SOURCES app.c
    APP_LINK_LIBRARIES dl
    PLATFORMS linux
)
