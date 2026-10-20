#include "j10.h"
#include "board.h"
#include "config.h"
#include "symtab.h"

/* STM32L433 のデータシート（DS12469）の代替機能表: PA15 は AF1 = TIM2_CH1（TIM2_ETR）、PA1 は AF1 = TIM2_CH2。
 * 2 本とも AF1 */
#define J10_AF_TIM2          1u

#define PIN_A_IDX            15u   /* PA15 */
#define PIN_B_IDX            1u    /* PA1 */

#define MODER_INPUT          0u
#define MODER_AF             2u
#define MODER_ANALOG         3u
#define PUPD_NONE            0u
#define PUPD_UP              1u

#define SMS_ENCODER_TI12     3u    /* エンコーダモード 3: TI1・TI2 の両エッジで数える（4 逓倍） */
#define CCMR_CCS_TI          1u    /* CC1S/CC2S = 01: 入力、ICx は TIx にマップ */

static uint8_t applied = 0xFFu;    /* 端子に設定中のモード。0xFF = リセット直後のまま */

/* HOME 入力 1 本分の確定状態。確定待ちのサンプルが HOME_CONFIRM_SAMPLES 回連続したら確定する（入力ごとに独立） */
typedef struct {
    bool    active;    /* 確定した作動状態 */
    bool    cand;      /* 直近のサンプル */
    uint8_t count;     /* cand が連続した回数（0 = サンプルなし） */
} pin_deb_t;
static pin_deb_t hm1, hm2;

static void hm_sample(pin_deb_t *h, bool s)
{
    if (h->count != 0 && s == h->cand) {
        if (h->count < 0xFFu) h->count++;
    } else {
        h->cand = s;
        h->count = 1;
    }
    if (h->count >= HOME_CONFIRM_SAMPLES) {
        h->active = s;
    }
}

/* 1 ピンのモード・プル・代替機能を設定する（PA15/PA1 の GPIOA 専用） */
static void pin_cfg(uint32_t idx, uint32_t moder, uint32_t pupd, uint32_t af)
{
    GPIO_TypeDef *p = GPIOA;
    uint32_t sh2 = idx * 2u;
    uint32_t afi = idx >> 3;
    uint32_t sh4 = (idx & 7u) * 4u;

    p->AFR[afi] = (p->AFR[afi] & ~(0xFu << sh4)) | (af << sh4);
    p->PUPDR = (p->PUPDR & ~(3u << sh2)) | (pupd << sh2);
    p->MODER = (p->MODER & ~(3u << sh2)) | (moder << sh2);
}

static void pins_cfg(uint32_t moder, uint32_t pupd, uint32_t af)
{
    pin_cfg(PIN_A_IDX, moder, pupd, af);
    pin_cfg(PIN_B_IDX, moder, pupd, af);
}

static void tim2_stop(void)
{
    TIM2->CR1 = 0;
    TIM2->SMCR = 0;
    TIM2->CCER = 0;
    TIM2->CCMR1 = 0;
    __HAL_RCC_TIM2_CLK_DISABLE();
}

static void hm_reset(void)
{
    hm1 = (pin_deb_t){0};
    hm2 = (pin_deb_t){0};
}

void j10_init(void)
{
    applied = 0xFFu;
    hm_reset();
}

void j10_set_mode(uint8_t mode)
{
    if (mode > J10_MODE_HOME || mode == applied) {
        return;
    }

    /* 一度アナログ（プルなし）に戻し、TIM2 を止めてから新しい機能を設定する（FR-J10-02）。
     * PA15 はリセット直後 JTDI だが、デバッグは SWD だけなので上書きしてよい（FR-J10-04） */
    pins_cfg(MODER_ANALOG, PUPD_NONE, 0);
    tim2_stop();
    hm_reset();

    if (mode == J10_MODE_ENC) {
        __HAL_RCC_TIM2_CLK_ENABLE();
        TIM2->CR1 = 0;
        TIM2->PSC = 0;
        TIM2->ARR = 0xFFFFFFFFu;                    /* 32bit をそのまま使う（FR-ENC-03） */
        TIM2->CCMR1 = (CCMR_CCS_TI << TIM_CCMR1_CC1S_Pos) | (ENC_FILTER_ICF << TIM_CCMR1_IC1F_Pos) |
                      (CCMR_CCS_TI << TIM_CCMR1_CC2S_Pos) | (ENC_FILTER_ICF << TIM_CCMR1_IC2F_Pos);
        TIM2->SMCR = SMS_ENCODER_TI12 << TIM_SMCR_SMS_Pos;
        TIM2->CCER = TIM_CCER_CC1E | TIM_CCER_CC2E;   /* 極性は反転しない（カウント方向は配線のとおり。FR-ENC-03） */
        pins_cfg(MODER_AF, PUPD_UP, J10_AF_TIM2);   /* 内部プルアップ（オープンコレクタ出力のエンコーダ向け。FR-ENC-01） */
        TIM2->CNT = 0;
        TIM2->CR1 = TIM_CR1_CEN;
    } else if (mode == J10_MODE_HOME) {
        pins_cfg(MODER_INPUT, PUPD_UP, 0);          /* FR-HOME-01 */
    }
    applied = mode;
}

uint8_t j10_mode(void)
{
    return applied == 0xFFu ? J10_MODE_NONE : applied;
}

int32_t j10_enc_count(void)
{
    return applied == J10_MODE_ENC ? (int32_t)TIM2->CNT : 0;
}

void j10_poll_1ms(bool active_high)
{
    if (applied != J10_MODE_HOME) {
        hm_reset();
        return;
    }
    hm_sample(&hm1, ((J10_A_HOME1_PORT->IDR & J10_A_HOME1_PIN) != 0) == active_high);
    hm_sample(&hm2, ((J10_B_HOME2_PORT->IDR & J10_B_HOME2_PIN) != 0) == active_high);
}

bool j10_home1_active(void)
{
    return hm1.active;
}

bool j10_home2_active(void)
{
    return hm2.active;
}
