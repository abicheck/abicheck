#!/bin/sh
set -e
cd "$(dirname "$0")"
rm -rf env && mkdir -p env/sdk env/lib env/bin
CXX=${CXX:-c++}
$CXX -shared -fPIC -g -Isdk -Wl,-soname,libwidget.so.1 -o env/sdk/libwidget.so.1 sdk/widget.cpp
ln -sf libwidget.so.1 env/sdk/libwidget.so
$CXX -g -Isdk -o env/bin/app app/main.cpp -Lenv/sdk -lwidget
$CXX -shared -fPIC -g -Iinstalled -D_GLIBCXX_USE_CXX11_ABI=0 -Wl,-soname,libwidget.so.1 -o env/lib/libwidget.so.1 installed/widget.cpp
cat > env/run.sh <<'RUN'
#!/bin/sh
here="$(cd "$(dirname "$0")" && pwd)"
LD_LIBRARY_PATH="$here/lib" exec "$here/bin/app" "$@"
RUN
chmod +x env/run.sh
