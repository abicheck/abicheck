abicheck_add_case(case18_dependency_leak
    V1_SOURCES libfoo_v1.c
    V2_SOURCES libfoo_v2.c
    APP_SOURCES app.c
    PLATFORMS linux macos windows
)
