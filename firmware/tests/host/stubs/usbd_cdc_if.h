/*
 * ホスト単体テスト用の最小スタブ。実機の usbd_cdc_if.h（HAL・USB ミドルウェア）の代わりに
 * usb_proto.c が使う分だけを宣言する。実体は ../stubs.c。
 */
#ifndef USBD_CDC_IF_H
#define USBD_CDC_IF_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define USBD_OK    0u
#define USBD_BUSY  1u

#define USBD_STATE_DEFAULT     1u
#define USBD_STATE_ADDRESSED   2u
#define USBD_STATE_CONFIGURED  3u
#define USBD_STATE_SUSPENDED   4u

typedef struct {
    uint32_t TxState;
} USBD_CDC_HandleTypeDef;

typedef struct {
    uint8_t dev_state;
    void   *pClassData;
} USBD_HandleTypeDef;

uint8_t  CDC_Transmit_FS(uint8_t *Buf, uint16_t Len);
uint32_t HAL_GetTick(void);
uint32_t __get_PRIMASK(void);
void     __set_PRIMASK(uint32_t v);

/* usb_proto.c の USB_MEM_RD8 / USB_MEM_RD32（-D で差し替え）。偽のメモリ内容を返す */
uint8_t  test_rd8(uint32_t addr);
uint32_t test_rd32(uint32_t addr);

#endif /* USBD_CDC_IF_H */
