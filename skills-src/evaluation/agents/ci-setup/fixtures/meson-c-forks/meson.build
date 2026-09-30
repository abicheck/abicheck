project('ring', 'c', version : '3.0.1', default_options : ['default_library=shared', 'buildtype=release'])
inc = include_directories('include')
ring = library('ring', 'src/ring.c', include_directories : inc, version : '3.0.1', soversion : '3', install : true)
install_subdir('include/ring', install_dir : get_option('includedir'))
