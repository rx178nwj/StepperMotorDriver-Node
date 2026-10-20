/* ホスト単体テスト用の最小スタブ（position.c が参照する hi2c1 のみ）。実体は ../stubs_hw.c。 */
#ifndef I2C_H
#define I2C_H

#include "stm32l4xx_hal.h"

extern I2C_HandleTypeDef hi2c1;

#endif /* I2C_H */
