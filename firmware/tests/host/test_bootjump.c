/*
 * bootjump.c のホスト単体テスト（要求仕様 FR-BL-04）。
 * ROM へ飛ぶ部分 bootjump_to_rom は偽物に差し替え、フラグの判定だけを検証する。
 */
#include "bootjump.h"
#include "config.h"
#include <stdio.h>

static int g_checks;
static int g_fail;

#define CHECK(cond) \
    do { \
        g_checks++; \
        if (!(cond)) { \
            g_fail++; \
            printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        } \
    } while (0)

#define RUN(fn) \
    do { \
        int before_ = g_fail; \
        fn(); \
        printf("%s %s\n", (g_fail == before_) ? "PASS" : "FAIL", #fn); \
    } while (0)

static int g_jumps;
static int g_flag_set_at_jump;

void bootjump_to_rom(void)
{
    g_jumps++;
    g_flag_set_at_jump = bootjump_flag_is_set();
}

/* 起動直後でフラグが立っていない（初期値 0 のゼロ初期化領域）: 何もしない */
static void test_no_flag(void)
{
    g_jumps = 0;
    bootjump_check();
    CHECK(g_jumps == 0);
    CHECK(!bootjump_flag_is_set());
}

/* フラグを書いてリセット相当の bootjump_check: 飛ぶ。飛ぶ前にフラグは消えている。2 回目は飛ばない */
static void test_flag_jumps_once(void)
{
    g_jumps = 0;
    bootjump_request();
    CHECK(bootjump_flag_is_set());
    bootjump_check();
    CHECK(g_jumps == 1);
    CHECK(g_flag_set_at_jump == 0);   /* 先にフラグを消してから飛ぶ */
    CHECK(!bootjump_flag_is_set());
    bootjump_check();
    CHECK(g_jumps == 1);
}

int main(void)
{
    RUN(test_no_flag);
    RUN(test_flag_jumps_once);
    CHECK(BOOT_FLAG_MAGIC != 0u && BOOT_FLAG_MAGIC != 0xFFFFFFFFu);   /* 全 0・全 1 の RAM では移らない */
    printf("\n%d checks, %d failed\n", g_checks, g_fail);
    return g_fail == 0 ? 0 : 1;
}
