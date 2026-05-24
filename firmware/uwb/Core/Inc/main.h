/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for main.c file.
  *                   This file contains the common defines of the application.
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2025 STMicroelectronics.
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
#include "stm32f4xx_hal.h"

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
#define LED_USR_Pin GPIO_PIN_13
#define LED_USR_GPIO_Port GPIOC
#define BTN_USR_Pin GPIO_PIN_0
#define BTN_USR_GPIO_Port GPIOA
#define BTN_USR_EXTI_IRQn EXTI0_IRQn
#define UART2_BLE_TX_Pin GPIO_PIN_2
#define UART2_BLE_TX_GPIO_Port GPIOA
#define UART2_BLE_RX_Pin GPIO_PIN_3
#define UART2_BLE_RX_GPIO_Port GPIOA
#define UWB_IRQ_Pin GPIO_PIN_4
#define UWB_IRQ_GPIO_Port GPIOA
#define UWB_IRQ_EXTI_IRQn EXTI4_IRQn
#define AUX0_Pin GPIO_PIN_0
#define AUX0_GPIO_Port GPIOB
#define AUX1_Pin GPIO_PIN_1
#define AUX1_GPIO_Port GPIOB
#define UWB_RST_Pin GPIO_PIN_2
#define UWB_RST_GPIO_Port GPIOB
#define SPI1_CS1_Pin GPIO_PIN_12
#define SPI1_CS1_GPIO_Port GPIOB
#define SPI1_CS2_Pin GPIO_PIN_13
#define SPI1_CS2_GPIO_Port GPIOB
#define IMU_IRQ_Pin GPIO_PIN_15
#define IMU_IRQ_GPIO_Port GPIOB
#define IMU_IRQ_EXTI_IRQn EXTI15_10_IRQn
#define CHARGE_EN_Pin GPIO_PIN_3
#define CHARGE_EN_GPIO_Port GPIOB
#define DIP1_Pin GPIO_PIN_5
#define DIP1_GPIO_Port GPIOB
#define DIP1_EXTI_IRQn EXTI9_5_IRQn
#define DIP2_Pin GPIO_PIN_6
#define DIP2_GPIO_Port GPIOB
#define DIP2_EXTI_IRQn EXTI9_5_IRQn
#define DIP3_Pin GPIO_PIN_7
#define DIP3_GPIO_Port GPIOB
#define DIP3_EXTI_IRQn EXTI9_5_IRQn

/* USER CODE BEGIN Private defines */

/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
