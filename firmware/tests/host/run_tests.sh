#!/bin/sh
# usb_proto.c / symtab.c のホスト単体テストをビルドして実行する（gcc 必須）。run_tests.ps1 と同じ内容。
set -e
here=$(cd "$(dirname "$0")" && pwd)
app="$here/../../App"
out="$here/build"
mkdir -p "$out"
${CC:-gcc} -std=gnu11 -Wall -Wextra -g -O0 \
    -DUSB_MEM_RD8=test_rd8 -DUSB_MEM_RD32=test_rd32 \
    -I"$here/stubs" -I"$here" -I"$app/Inc" \
    "$here/test_usb.c" "$here/stubs.c" "$app/Src/usb_proto.c" "$app/Src/symtab.c" \
    -o "$out/test_usb"
"$out/test_usb"
