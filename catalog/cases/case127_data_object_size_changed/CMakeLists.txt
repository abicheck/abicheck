abicheck_add_case(case127_data_object_size_changed
    V1_SOURCES v1.c
    V2_SOURCES v2.c
    V1_HEADERS v1.h
    V2_HEADERS v2.h
    APP_SOURCES app.c
    # Non-PIE consumer: forces an R_X86_64_COPY relocation for config_table
    # under both GCC and Clang (Clang's PIE default uses GLOB_DAT instead).
    APP_COMPILE_OPTIONS -fno-pie
    APP_LINK_LIBRARIES -no-pie
    PLATFORMS linux
)
