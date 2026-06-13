abicheck_add_case(case132_threadsafe_statics_flip
    LANG CXX
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    V1_COMPILE_OPTIONS -fthreadsafe-statics
    V2_COMPILE_OPTIONS -fno-threadsafe-statics
    APP_SOURCES app.cpp
    PLATFORMS linux
)
