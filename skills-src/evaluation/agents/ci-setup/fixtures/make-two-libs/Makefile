CC ?= cc
CFLAGS ?= -O2
CFLAGS += -fPIC -Iinclude
all: build/libalpha.so.2 build/libbeta.so.1
build:
	mkdir -p build
build/libalpha.so.2: src/alpha.c | build
	$(CC) $(CFLAGS) -shared -Wl,-soname,libalpha.so.2 -o $@ $<
	ln -sf libalpha.so.2 build/libalpha.so
build/libbeta.so.1: src/beta.c build/libalpha.so.2 | build
	$(CC) $(CFLAGS) -shared -Wl,-soname,libbeta.so.1 -o $@ $< -Lbuild -lalpha
	ln -sf libbeta.so.1 build/libbeta.so
build/test_helper.so: src/test_helper.c | build
	$(CC) $(CFLAGS) -shared -o $@ $<
clean:
	rm -rf build
.PHONY: all clean
