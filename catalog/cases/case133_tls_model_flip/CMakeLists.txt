abicheck_add_case(case133_tls_model_flip
    LANG CXX
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    V1_COMPILE_OPTIONS -ftls-model=global-dynamic
    V2_COMPILE_OPTIONS -ftls-model=initial-exec
    APP_SOURCES app.cpp
    PLATFORMS linux
)
