abicheck_add_bundle_case(case90_bundle_intra_dep_removed
    LIBRARIES   libcore libalgo
    LINK_GRAPH  libalgo:libcore
    HEADERS     core.h algo.h
    PLATFORMS   linux
)
