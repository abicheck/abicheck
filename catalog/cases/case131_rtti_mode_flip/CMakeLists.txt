abicheck_add_case(case131_rtti_mode_flip
    LANG CXX
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    V1_COMPILE_OPTIONS -frtti
    V2_COMPILE_OPTIONS -fno-rtti
    APP_SOURCES app.cpp
    PLATFORMS linux
)
