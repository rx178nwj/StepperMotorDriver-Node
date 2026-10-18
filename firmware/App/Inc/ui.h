/*
 * ui.h — LED 表示とスイッチ入力（1 ms 周期で呼ぶ）
 */
#ifndef UI_H
#define UI_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    bool enabled;        /* 励磁中 */
    bool moving;
    uint8_t fault_code;  /* 0 = 異常なし、1〜 = 点滅回数 */
    bool temp_warn;
    uint32_t comm_count; /* 変化したら COMM LED を点灯 */
    bool comm_error;     /* CAN がエラーパッシブ／バスオフ: COMM LED を 2 Hz で点滅 */
} ui_view_t;

void ui_update(uint32_t now_ms, const ui_view_t *v);
void ui_scan_switches(void);
bool ui_sw1(void);
bool ui_sw2(void);

#endif /* UI_H */
