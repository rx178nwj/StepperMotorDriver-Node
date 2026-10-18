#include "comm.h"
#include "board.h"
#include <string.h>

/* 80 MHz PCLK1。PRESC=1, SCLDEL=9 → データセットアップ 250 ns（標準/ファスト両対応） */
#define I2C_TIMING  0x10909CECu
#define QUEUE_LEN   4u
#define TX_LEN      (REG_MAP_SIZE + 32u)   /* 末尾を超えた読み出しは 0xFF */

static I2C_HandleTypeDef hi2c;

static uint8_t image[REG_MAP_SIZE];
static uint8_t tx_buf[TX_LEN];
static uint8_t rx_buf[COMM_RX_MAX];
static uint8_t rx_byte;
static uint8_t rx_count;
static uint8_t reg_ptr;

static comm_write_t queue[QUEUE_LEN];
static volatile uint8_t q_head;   /* ISR が書く */
static volatile uint8_t q_tail;   /* main が書く */

static volatile uint32_t last_access_ms;
static volatile uint32_t access_count;

void comm_init(uint8_t addr7)
{
    __HAL_RCC_I2C2_CLK_ENABLE();

    hi2c.Instance = I2C2;
    hi2c.Init.Timing = I2C_TIMING;
    hi2c.Init.OwnAddress1 = (uint32_t)addr7 << 1;
    hi2c.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
    hi2c.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
    hi2c.Init.OwnAddress2 = 0;
    hi2c.Init.OwnAddress2Masks = I2C_OA2_NOMASK;
    hi2c.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
    hi2c.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
    HAL_I2C_Init(&hi2c);
    HAL_I2CEx_ConfigAnalogFilter(&hi2c, I2C_ANALOGFILTER_ENABLE);

    HAL_NVIC_SetPriority(I2C2_EV_IRQn, IRQ_PRIO_I2C, 0);
    HAL_NVIC_SetPriority(I2C2_ER_IRQn, IRQ_PRIO_I2C, 0);
    HAL_NVIC_EnableIRQ(I2C2_EV_IRQn);
    HAL_NVIC_EnableIRQ(I2C2_ER_IRQn);

    HAL_I2C_EnableListen_IT(&hi2c);
}

void comm_update_image(const uint8_t *src, uint32_t len)
{
    if (len > REG_MAP_SIZE) {
        len = REG_MAP_SIZE;
    }
    /* I2C 割り込みだけを止めて、読み出し途中のスナップショット作成と競合させない */
    HAL_NVIC_DisableIRQ(I2C2_EV_IRQn);
    memcpy(image, src, len);
    HAL_NVIC_EnableIRQ(I2C2_EV_IRQn);
}

bool comm_pop_write(comm_write_t *out)
{
    if (q_tail == q_head) {
        return false;
    }
    *out = queue[q_tail];
    q_tail = (uint8_t)((q_tail + 1u) % QUEUE_LEN);
    return true;
}

uint32_t comm_last_access_ms(void)
{
    return last_access_ms;
}

uint32_t comm_access_count(void)
{
    return access_count;
}

/* REG のみの書き込みはポインタ設定、データ付きはキューへ */
static void flush_rx(void)
{
    if (rx_count >= 1u) {
        reg_ptr = rx_buf[0];
    }
    if (rx_count >= 2u) {
        uint8_t next = (uint8_t)((q_head + 1u) % QUEUE_LEN);
        if (next != q_tail) {   /* 満杯なら破棄 */
            uint8_t n = rx_count > COMM_RX_MAX ? COMM_RX_MAX : rx_count;
            queue[q_head].len = n;
            memcpy(queue[q_head].data, rx_buf, n);
            q_head = next;
        }
    }
    rx_count = 0;
}

void HAL_I2C_AddrCallback(I2C_HandleTypeDef *h, uint8_t direction, uint16_t addr_match)
{
    (void)addr_match;
    last_access_ms = HAL_GetTick();
    access_count++;

    if (direction == I2C_DIRECTION_TRANSMIT) {
        /* マスター → スレーブ書き込み。1 バイトずつ受ける */
        flush_rx();
        HAL_I2C_Slave_Seq_Receive_IT(h, &rx_byte, 1, I2C_NEXT_FRAME);
    } else {
        /* 直前の書き込みフェーズ（リピートスタート）を確定してから読み出し */
        flush_rx();
        for (uint32_t i = 0; i < TX_LEN; i++) {
            uint32_t a = (uint32_t)reg_ptr + i;
            tx_buf[i] = a < REG_MAP_SIZE ? image[a] : 0xFFu;
        }
        HAL_I2C_Slave_Seq_Transmit_IT(h, tx_buf, TX_LEN, I2C_LAST_FRAME);
    }
}

void HAL_I2C_SlaveRxCpltCallback(I2C_HandleTypeDef *h)
{
    if (rx_count < COMM_RX_MAX) {
        rx_buf[rx_count] = rx_byte;
    }
    if (rx_count < 0xFFu) {
        rx_count++;
    }
    HAL_I2C_Slave_Seq_Receive_IT(h, &rx_byte, 1, I2C_NEXT_FRAME);
}

/* STOP で呼ばれる（受信待ち・送信残りがある場合は ErrorCallback(AF) の後に来る） */
void HAL_I2C_ListenCpltCallback(I2C_HandleTypeDef *h)
{
    flush_rx();
    HAL_I2C_EnableListen_IT(h);
}

void HAL_I2C_ErrorCallback(I2C_HandleTypeDef *h)
{
    /* AF はマスターの NACK による正常終了。その他（BERR/ARLO/OVR）は待ち受けを再開 */
    if (HAL_I2C_GetState(h) == HAL_I2C_STATE_READY) {
        flush_rx();
        HAL_I2C_EnableListen_IT(h);
    }
}

void comm_ev_isr(void)
{
    HAL_I2C_EV_IRQHandler(&hi2c);
}

void comm_er_isr(void)
{
    HAL_I2C_ER_IRQHandler(&hi2c);
}
