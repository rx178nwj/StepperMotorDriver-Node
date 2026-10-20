# usb_proto.c / symtab.c のホスト単体テストをビルドして実行する。
# 必要なもの: gcc（MinGW-w64 / MSYS2 / Linux の gcc など。GNU 拡張のインラインアセンブラを使うため gcc 系）。
# FW 本体の ARM ビルド（firmware/CMakeLists.txt）とは独立。成果物は build/ に出る。
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

$exe = Join-Path $out 'test_usb.exe'
& $cc -std=gnu11 -Wall -Wextra -g -O0 `
    '-DUSB_MEM_RD8=test_rd8' '-DUSB_MEM_RD32=test_rd32' `
    "-I$here\stubs" "-I$here" "-I$app\Inc" `
    "$here\test_usb.c" "$here\stubs.c" "$app\Src\usb_proto.c" "$app\Src\symtab.c" `
    -o $exe
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $exe
exit $LASTEXITCODE
