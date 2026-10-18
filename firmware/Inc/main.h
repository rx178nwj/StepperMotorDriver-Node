/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for main.c file.
  *                   This file contains the common defines of the application.
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32l4xx_hal.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */

/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */

/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/
void Error_Handler(void);

/* USER CODE BEGIN EFP */

/* USER CODE END EFP */

/* Private defines -----------------------------------------------------------*/
#define ADDR2_Pin GPIO_PIN_13
#define ADDR2_GPIO_Port GPIOC
#define PWR_ST_Pin GPIO_PIN_14
#define PWR_ST_GPIO_Port GPIOC
#define DRV_MO_Pin GPIO_PIN_15
#define DRV_MO_GPIO_Port GPIOC
#define ADDR0_Pin GPIO_PIN_0
#define ADDR0_GPIO_Port GPIOH
#define ADDR1_Pin GPIO_PIN_1
#define ADDR1_GPIO_Port GPIOH
#define VM_SENSE_Pin GPIO_PIN_0
#define VM_SENSE_GPIO_Port GPIOA
#define DRV_LO1_Pin GPIO_PIN_2
#define DRV_LO1_GPIO_Port GPIOA
#define DRV_LO2_Pin GPIO_PIN_3
#define DRV_LO2_GPIO_Port GPIOA
#define DAC_VREF_Pin GPIO_PIN_4
#define DAC_VREF_GPIO_Port GPIOA
#define LED_RUN_Pin GPIO_PIN_5
#define LED_RUN_GPIO_Port GPIOA
#define TEMP_Pin GPIO_PIN_6
#define TEMP_GPIO_Port GPIOA
#define AD_IN_Pin GPIO_PIN_7
#define AD_IN_GPIO_Port GPIOA
#define LED_MCU_Pin GPIO_PIN_0
#define LED_MCU_GPIO_Port GPIOB
#define LED_COMM_Pin GPIO_PIN_1
#define LED_COMM_GPIO_Port GPIOB
#define LED_FAULT_Pin GPIO_PIN_2
#define LED_FAULT_GPIO_Port GPIOB
#define I2C_PC_SCL_Pin GPIO_PIN_10
#define I2C_PC_SCL_GPIO_Port GPIOB
#define I2C_PC_SDA_Pin GPIO_PIN_11
#define I2C_PC_SDA_GPIO_Port GPIOB
#define DRV_DMODE0_Pin GPIO_PIN_12
#define DRV_DMODE0_GPIO_Port GPIOB
#define DRV_DMODE1_Pin GPIO_PIN_13
#define DRV_DMODE1_GPIO_Port GPIOB
#define DRV_DMODE2_Pin GPIO_PIN_14
#define DRV_DMODE2_GPIO_Port GPIOB
#define DRV_RESET_Pin GPIO_PIN_15
#define DRV_RESET_GPIO_Port GPIOB
#define DRV_CLK_Pin GPIO_PIN_8
#define DRV_CLK_GPIO_Port GPIOA
#define DRV_DIR_Pin GPIO_PIN_9
#define DRV_DIR_GPIO_Port GPIOA
#define DRV_EN_Pin GPIO_PIN_10
#define DRV_EN_GPIO_Port GPIOA
#define SWITCH1_Pin GPIO_PIN_4
#define SWITCH1_GPIO_Port GPIOB
#define SWITCH2_Pin GPIO_PIN_5
#define SWITCH2_GPIO_Port GPIOB
#define I2C_SENS_SCL_Pin GPIO_PIN_6
#define I2C_SENS_SCL_GPIO_Port GPIOB
#define I2C_SENS_SDA_Pin GPIO_PIN_7
#define I2C_SENS_SDA_GPIO_Port GPIOB
#define DRV_AGC0_Pin GPIO_PIN_8
#define DRV_AGC0_GPIO_Port GPIOB
#define DRV_AGC1_Pin GPIO_PIN_9
#define DRV_AGC1_GPIO_Port GPIOB

/* USER CODE BEGIN Private defines */

/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
