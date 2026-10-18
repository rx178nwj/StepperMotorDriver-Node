/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file    gpio.c
  * @brief   This file provides code for the configuration
  *          of all used GPIO pins.
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

/* Includes ------------------------------------------------------------------*/
#include "gpio.h"

/* USER CODE BEGIN 0 */

/* USER CODE END 0 */

/*----------------------------------------------------------------------------*/
/* Configure GPIO                                                             */
/*----------------------------------------------------------------------------*/
/* USER CODE BEGIN 1 */

/* USER CODE END 1 */

/** Configure pins as
        * Analog
        * Input
        * Output
        * EVENT_OUT
        * EXTI
*/
void MX_GPIO_Init(void)
{

  GPIO_InitTypeDef GPIO_InitStruct = {0};

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOA, LED_RUN_Pin|DRV_DIR_Pin|DRV_EN_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOB, LED_MCU_Pin|LED_COMM_Pin|LED_FAULT_Pin|DRV_DMODE0_Pin
                          |DRV_DMODE1_Pin|DRV_DMODE2_Pin|DRV_RESET_Pin|DRV_AGC0_Pin
                          |DRV_AGC1_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pins : ADDR2_Pin PWR_ST_Pin DRV_MO_Pin */
  GPIO_InitStruct.Pin = ADDR2_Pin|PWR_ST_Pin|DRV_MO_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);

  /*Configure GPIO pins : ADDR0_Pin ADDR1_Pin */
  GPIO_InitStruct.Pin = ADDR0_Pin|ADDR1_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOH, &GPIO_InitStruct);

  /*Configure GPIO pins : DRV_LO1_Pin DRV_LO2_Pin */
  GPIO_InitStruct.Pin = DRV_LO1_Pin|DRV_LO2_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_IT_FALLING;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  /*Configure GPIO pins : LED_RUN_Pin DRV_DIR_Pin DRV_EN_Pin */
  GPIO_InitStruct.Pin = LED_RUN_Pin|DRV_DIR_Pin|DRV_EN_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  /*Configure GPIO pins : LED_MCU_Pin LED_COMM_Pin LED_FAULT_Pin DRV_DMODE0_Pin
                           DRV_DMODE1_Pin DRV_DMODE2_Pin DRV_RESET_Pin DRV_AGC0_Pin
                           DRV_AGC1_Pin */
  GPIO_InitStruct.Pin = LED_MCU_Pin|LED_COMM_Pin|LED_FAULT_Pin|DRV_DMODE0_Pin
                          |DRV_DMODE1_Pin|DRV_DMODE2_Pin|DRV_RESET_Pin|DRV_AGC0_Pin
                          |DRV_AGC1_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  /*Configure GPIO pins : SWITCH1_Pin SWITCH2_Pin */
  GPIO_InitStruct.Pin = SWITCH1_Pin|SWITCH2_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

}

/* USER CODE BEGIN 2 */

/* USER CODE END 2 */
