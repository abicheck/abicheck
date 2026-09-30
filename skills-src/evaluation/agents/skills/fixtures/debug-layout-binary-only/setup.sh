#!/bin/sh
set -e
cd "$(dirname "$0")"
rm -rf env && mkdir -p env/sdk env/lib env/bin
CC=${CC:-cc}
$CC -shared -fPIC -g -Isdk -Wl,-soname,libwidget.so.1 -o env/sdk/libwidget.so.1 sdk/widget.c
ln -sf libwidget.so.1 env/sdk/libwidget.so
$CC -g -Isdk -o env/bin/app app/main.c -Lenv/sdk -lwidget
cp prebuilt/libwidget.so.1 env/lib/libwidget.so.1
cat > env/run.sh <<'RUN'
#!/bin/sh
here="$(cd "$(dirname "$0")" && pwd)"
LD_LIBRARY_PATH="$here/lib" exec "$here/bin/app" "$@"
RUN
chmod +x env/run.sh
