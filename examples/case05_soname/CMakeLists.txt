abicheck_add_case(case05_soname
    V1_SOURCES old/lib.c
    V2_SOURCES new/lib.c
    V2_LINK_OPTIONS $<$<NOT:$<CXX_COMPILER_ID:MSVC>>:-Wl,-soname,libv2.so>
    APP_SOURCES app.c
    PLATFORMS linux
)
