abicheck_add_case(case130_exceptions_mode_flip
    LANG CXX
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    V1_COMPILE_OPTIONS -fexceptions
    V2_COMPILE_OPTIONS -fno-exceptions
    APP_SOURCES app.cpp
    PLATFORMS linux
)
