#include "bootjump.h"
#include "config.h"

#ifdef __arm__
#include "stm32l4xx_hal.h"
/* リンカスクリプトの .noinit（スタートアップがゼロクリアも初期値コピーもしない。SRAM1 内、FR-BL-04）。
 * SRAM2 はオプションバイト SRAM2_RST によってはリセットで消えるため使わない */
#define BOOT_NOINIT  __attribute__((section(".noinit")))
#else
#define BOOT_NOINIT
#endif

static volatile uint32_t boot_flag BOOT_NOINIT;

void bootjump_request(void)
{
    boot_flag = BOOT_FLAG_MAGIC;
}

bool bootjump_flag_is_set(void)
{
    return boot_flag == BOOT_FLAG_MAGIC;
}

#ifdef __arm__
/*
 * STM32L4 の ROM ブートローダ（システムメモリ 0x1FFF0000）へ飛ぶ。ホストテストでは差し替える。
 * リセット直後に呼ばれるので、NVIC・SysTick・周辺はリセット状態（クロックは MSI 4 MHz）。
 *   1. SYSCFG のクロックを有効にし、メモリ再配置（MEMRMP.MEM_MODE）をシステムメモリ（001）にする。
 *      ROM が 0x00000000 からも見えるようになり、ROM のコードが 0 番地基準で動いても整合する
 *   2. VTOR をシステムメモリの先頭にする（SystemInit が Flash 先頭へ設定済みのため）
 *   3. 0x1FFF0000 の語を MSP、0x1FFF0004 の語（リセットベクタ）へ分岐する
 * 割り込みはどれも有効にしていない（PRIMASK も触らない）ので、ROM はリセット直後と同じ状態から始まる
 */
void bootjump_to_rom(void)
{
    const uint32_t rom_base = 0x1FFF0000u;

    RCC->APB2ENR |= RCC_APB2ENR_SYSCFGEN;
    (void)RCC->APB2ENR;   /* クロック有効化の反映を待つ（読み戻し） */
    SYSCFG->MEMRMP = (SYSCFG->MEMRMP & ~SYSCFG_MEMRMP_MEM_MODE) | SYSCFG_MEMRMP_MEM_MODE_0;
    SCB->VTOR = rom_base;
    __DSB();
    __ISB();

    uint32_t msp = *(volatile const uint32_t *)(uintptr_t)rom_base;
    uint32_t entry = *(volatile const uint32_t *)(uintptr_t)(rom_base + 4u);
    __set_MSP(msp);
    ((void (*)(void))entry)();
    for (;;) {
    }
}
#else
/* ホストテストが実体を与える */
extern void bootjump_to_rom(void);
#endif

void bootjump_check(void)
{
    /* マジック値との完全一致だけを見る。電源投入時の RAM の不定値で誤って移らない（FR-BL-04） */
    if (boot_flag != BOOT_FLAG_MAGIC) {
        return;
    }
    boot_flag = 0u;   /* 先に消す。ROM から戻ってきても（リセットしても）繰り返し移らない */
    bootjump_to_rom();
}
