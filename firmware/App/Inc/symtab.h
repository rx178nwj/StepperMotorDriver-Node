/*
 * symtab.h — 名前表（USB の名前指定読み出し用、要求仕様 FR-USB-35〜38）
 *
 * 名前表は Flash 上の const 配列。登録はマクロ 1 行で行う。
 * エントリは専用セクション .sym_table に置かれ、リンカスクリプト（App/STM32L433xx_app.ld）が
 * 全ファイル分を連結して __sym_table_start 〜 __sym_table_end にまとめる。
 * そのため、変数が static のままでも、その変数を定義した .c ファイルの末尾で登録できる
 * （他のファイルへ extern 化しなくてよい）。
 *
 *   SYM_ENTRY(position, SYM_I32, 1);              名前 "position"、&position、int32、1 要素
 *   SYM_ENTRY(cfg.max_speed, SYM_U32, 1);        名前 "cfg.max_speed"（式がそのまま名前になる）
 *   SYM_ENTRY(cfg, SYM_BYTES, sizeof(cfg));      構造体全体（バイト列、NUM = バイト数）
 *   SYM_ENTRY_AS("plan_speed", v, SYM_F32, 1);   名前を変数名と別にしたいとき
 *
 * 変数のサイズ（sizeof）と 要素サイズ × NUM が一致しないとコンパイルエラーになる。
 * 名前は ASCII 31 文字以内（超えるとコンパイルエラー）。
 * 登録順はリンク順で決まるため INDEX は版ごとに変わりうる。PC は SYM_FIND で名前から引くこと。
 */
#ifndef SYMTAB_H
#define SYMTAB_H

#include <stdbool.h>
#include <stdint.h>

/* TYPE（FR-USB-36） */
#define SYM_U8     0x01u
#define SYM_I8     0x02u
#define SYM_U16    0x03u
#define SYM_I16    0x04u
#define SYM_U32    0x05u
#define SYM_I32    0x06u
#define SYM_F32    0x07u
#define SYM_BYTES  0x10u   /* 構造体など。要素サイズ 1、NUM = バイト数 */

#define SYM_NAME_MAX  31u

typedef struct {
    const char *name;   /* ASCII、終端 '\0' 付き、31 文字以内 */
    const void *addr;
    uint16_t    num;    /* 要素数 */
    uint8_t     type;   /* SYM_* */
    uint8_t     reserved;
} sym_entry_t;

#define SYM_ELEM_SIZE(type) \
    ((type) == SYM_U8 || (type) == SYM_I8 || (type) == SYM_BYTES ? 1u : \
     (type) == SYM_U16 || (type) == SYM_I16 ? 2u : 4u)

#define SYM_CAT2(a, b) a##b
#define SYM_CAT(a, b)  SYM_CAT2(a, b)

#define SYM_ENTRY_AS(namestr, var, type, n)                                                     \
    _Static_assert(sizeof(namestr) <= SYM_NAME_MAX + 1u, "symtab: name longer than 31 chars");  \
    _Static_assert(sizeof(var) == SYM_ELEM_SIZE(type) * (n), "symtab: size != elem size * NUM");\
    static const sym_entry_t SYM_CAT(sym_entry_, __COUNTER__)                                   \
        __attribute__((section(".sym_table"), used)) = {                                        \
        (namestr), (const void *)&(var), (uint16_t)(n), (uint8_t)(type), 0u }

#define SYM_ENTRY(var, type, n)  SYM_ENTRY_AS(#var, var, type, n)

/* ---- 検索 API（usb_proto.c から使う） ---- */

uint16_t symtab_count(void);
const sym_entry_t *symtab_get(uint16_t index);
/* 名前（nlen バイト、終端不要）で完全一致検索（大文字小文字を区別）。見つからなければ -1 */
int32_t symtab_find(const uint8_t *name, uint8_t nlen);
/* 変数全体のバイト数 = 要素サイズ × NUM */
uint32_t symtab_size(const sym_entry_t *e);
uint8_t symtab_name_len(const sym_entry_t *e);

#endif /* SYMTAB_H */
