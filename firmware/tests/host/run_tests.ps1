# ホスト単体テストをビルドして実行する。run_tests.sh と同じ内容。
#   test_usb      : usb_proto.c / symtab.c
#   test_position : position.c の計算関数
#   test_stepper  : stepper.c の台形加減速（test_stepper.c が stepper.c を #include する）
#   test_settings : settings.c（不揮発設定。Flash は RAM 上の配列で模擬し、読み出しは -DNV_BASE -DNV_MEMCPY で差し替える）
# 必要なもの: gcc（MinGW-w64 / MSYS2 / Linux の gcc など。GNU 拡張のインラインアセンブラを使うため gcc 系）。
# FW 本体の ARM ビルド（firmware/CMakeLists.txt）とは独立。成果物は build/ に出る。
# どれかが失敗したら非 0 で終わる。
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$app = Join-Path $here '..\..\App'
$out = Join-Path $here 'build'
New-Item -ItemType Directory -Force -Path $out | Out-Null

$cc = if ($env:CC) { $env:CC } else { 'gcc' }
if (-not (Get-Command $cc -ErrorAction SilentlyContinue)) {
    Write-Error "$cc が見つかりません。gcc を PATH に入れるか、環境変数 CC で指定してください。"
    exit 2
}

$common = @('-std=gnu11', '-Wall', '-Wextra', '-g', '-O0')
$inc = @("-I$here\stubs", "-I$here", "-I$app\Inc")

# usb_proto.c / symtab.c
& $cc @common '-DUSB_MEM_RD8=test_rd8' '-DUSB_MEM_RD32=test_rd32' @inc `
    "$here\test_usb.c" "$here\stubs.c" "$app\Src\usb_proto.c" "$app\Src\symtab.c" `
    -o "$out\test_usb.exe"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# position.c
& $cc @common @inc `
    "$here\test_position.c" "$here\stubs_hw.c" "$app\Src\position.c" `
    -lm -o "$out\test_position.exe"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# stepper.c（ARM の FW は -fshort-enums 相当で motion_mode_t が 1 バイトなので、ホストでも同じフラグを付ける）
& $cc @common @inc '-fshort-enums' `
    "$here\test_stepper.c" "$here\stubs_hw.c" `
    -lm -o "$out\test_stepper.exe"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# settings.c
& $cc @common '-DNV_BASE=0x0803F000u' '-DNV_MEMCPY=test_nv_memcpy' @inc `
    "$here\test_settings.c" "$app\Src\settings.c" `
    -o "$out\test_settings.exe"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$rc = 0
foreach ($t in 'test_usb', 'test_position', 'test_stepper', 'test_settings') {
    Write-Host "=== $t"
    & "$out\$t.exe"
    if ($LASTEXITCODE -ne 0) { $rc = $LASTEXITCODE }
}
exit $rc
