/*
 * comm.h — I2C2 スレーブ（レジスタ方式）
 *
 * 書き込み: [REG][DATA...]  読み出し: [REG] → リピートスタート → 連続読み出し
 * 受信した書き込みはキューに積み、main ループで regs 側が処理する。
 */
#ifndef COMM_H
#define COMM_H

#include <stdbool.h>
#include <stdint.h>

#define COMM_RX_MAX   33u     /* REG + 32 バイト */
#define REG_MAP_SIZE  0x60u

typedef struct {
    uint8_t len;              /* REG を含むバイト数 */
    uint8_t data[COMM_RX_MAX];
} comm_write_t;

void comm_init(uint8_t addr7);
bool comm_pop_write(comm_write_t *out);

/* 読み出し用レジスタイメージを更新（main ループから） */
void comm_update_image(const uint8_t *image, uint32_t len);

/* 最後にアドレス一致した時刻 [ms] と、その回数 */
uint32_t comm_last_access_ms(void);
uint32_t comm_access_count(void);

void comm_ev_isr(void);
void comm_er_isr(void);

#endif /* COMM_H */
