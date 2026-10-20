#!/bin/sh
# ホスト単体テストをビルドして実行する（gcc 必須）。run_tests.ps1 と同じ内容。
#   test_usb      : usb_proto.c / symtab.c
#   test_position : position.c の計算関数
#   test_stepper  : stepper.c の台形加減速（test_stepper.c が stepper.c を #include する）
#     （ARM の FW は -fshort-enums 相当で motion_mode_t が 1 バイトなので、ホストでも同じフラグを付ける）
#   test_settings : settings.c（不揮発設定。Flash は RAM 上の配列で模擬し、読み出しは -DNV_BASE -DNV_MEMCPY で差し替える）
#   test_comm     : comm.c（CAN 通信層。app の関数は test_comm.c 内の偽物、HAL の CAN はスタブ）
#   test_app      : app.c のレジスタ処理（app_reg_read / app_reg_write。settings.c・j10.c・home.c は実物、stepper・drv などは test_app.c 内の偽物）
#   test_home     : home.c（原点復帰の状態機械。ハードウェアに触れない純粋な関数）
#   test_j10      : j10.c（PA15/PA1 の切り替え・エンコーダ・HOME 入力。GPIOA・TIM2 はスタブのレジスタの実体）
# どれかが失敗したら非 0 で終わる。
set -e
here=$(cd "$(dirname "$0")" && pwd)
app="$here/../../App"
out="$here/build"
mkdir -p "$out"
cc=${CC:-gcc}
flags="-std=gnu11 -Wall -Wextra -g -O0"

$cc $flags \
    -DUSB_MEM_RD8=test_rd8 -DUSB_MEM_RD32=test_rd32 \
    -I"$here/stubs" -I"$here" -I"$app/Inc" \
    "$here/test_usb.c" "$here/stubs.c" "$app/Src/usb_proto.c" "$app/Src/symtab.c" \
    -o "$out/test_usb"

$cc $flags \
    -I"$here/stubs" -I"$here" -I"$app/Inc" \
    "$here/test_position.c" "$here/stubs_hw.c" "$app/Src/position.c" \
    -lm -o "$out/test_position"

$cc $flags \
    -I"$here/stubs" -I"$here" -I"$app/Inc" \
    -fshort-enums \
    "$here/test_stepper.c" "$here/stubs_hw.c" \
    -lm -o "$out/test_stepper"

$cc $flags \
    -DNV_BASE=0x0803F000u -DNV_MEMCPY=test_nv_memcpy \
    -I"$here/stubs" -I"$here" -I"$app/Inc" \
    "$here/test_settings.c" "$app/Src/settings.c" \
    -o "$out/test_settings"

$cc $flags     -I"$here/stubs" -I"$here" -I"$app/Inc"     "$here/test_comm.c" "$here/stubs_hw.c" "$app/Src/comm.c"     -o "$out/test_comm"

$cc $flags     -DNV_BASE=0x0803F000u -DNV_MEMCPY=test_nv_memcpy     -I"$here/stubs" -I"$here" -I"$app/Inc"     -fshort-enums     "$here/test_app.c" "$here/stubs_hw.c" "$app/Src/app.c" "$app/Src/settings.c" "$app/Src/j10.c" "$app/Src/home.c"     -o "$out/test_app"

$cc $flags     -I"$here/stubs" -I"$here" -I"$app/Inc"     "$here/test_home.c" "$app/Src/home.c"     -o "$out/test_home"

$cc $flags     -I"$here/stubs" -I"$here" -I"$app/Inc"     "$here/test_j10.c" "$here/stubs_hw.c" "$app/Src/j10.c"     -o "$out/test_j10"

rc=0
for t in test_usb test_position test_stepper test_settings test_comm test_app test_home test_j10; do
    echo "=== $t"
    "$out/$t" || rc=1
done
exit $rc
