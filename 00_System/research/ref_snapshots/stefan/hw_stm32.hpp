/**
 * hw_stm32.hpp — hardware policy for the Nucleo-F446RE, built on the HAL.
 *
 * The only place in the project that knows about specific peripherals and
 * pins. The motion core (axis.hpp) does not depend on it.
 *
 * The timer and the GPIO ports are configured by CubeMX; this file only
 * drives them. Bind the timer handle once at startup with bind(&htim2) and
 * everything else goes through the HAL.
 *
 * Wiring:
 *   PA0  TIM2_CH1 (AF1)  -> PUL
 *   PA1  GPIO out        -> DIR
 *   PA4  GPIO out        -> ENA
 *   PB0  GPIO in, pull-up -> MIN limit switch (NC to ground)
 *   PB1  GPIO in, pull-up -> MAX limit switch
 *   PC13 GPIO in         -> E-STOP (blue B1 button)
 *
 * CubeMX requirements:
 *   TIM2 Channel1 as PWM Generation CH1, Prescaler 9-1, Counter Period
 *   0xFFFFFFFF, auto-reload preload ENABLE, TIM2 global interrupt enabled.
 */
#pragma once

#include "main.h"

#include <cstdint>

namespace stepctl {
    struct Stm32Hw {
        /* TIM2 is clocked from APB1x2 = 90 MHz; with Prescaler 9-1 that gives
           10 MHz, so one tick is 0.1 us. Set the prescaler in CubeMX. */
        static constexpr std::uint32_t kTimerHz = 10'000'000;

        /** Binds the CubeMX handle. Call once before Axis::init(). */
        static void bind(TIM_HandleTypeDef *htim) {
            htim_ = htim;
        }

        static void timerStart(std::uint32_t ticks) {
            __HAL_TIM_SET_COUNTER(htim_, 0);
            __HAL_TIM_SET_AUTORELOAD(htim_, ticks - 1);

            /* With auto-reload preload enabled the new period only reaches the
               shadow register on an update event, so force one before starting.
               It raises the update flag, which must be cleared or the first
               interrupt would fire immediately and emit a phantom step. */
            HAL_TIM_GenerateEvent(htim_, TIM_EVENTSOURCE_UPDATE);
            __HAL_TIM_CLEAR_IT(htim_, TIM_IT_UPDATE);

            HAL_TIM_PWM_Start(htim_, TIM_CHANNEL_1);
            HAL_TIM_Base_Start_IT(htim_);
        }

        static void timerStop() {
            HAL_TIM_Base_Stop_IT(htim_);
            HAL_TIM_PWM_Stop(htim_, TIM_CHANNEL_1); // parks PUL in its idle state
        }

        static void timerSetPeriod(std::uint32_t arr) {
            __HAL_TIM_SET_AUTORELOAD(htim_, arr);
        }

        static void timerSetPulseWidth(std::uint32_t ticks) {
            __HAL_TIM_SET_COMPARE(htim_, TIM_CHANNEL_1, ticks);
        }

        static void setDirectionPin(bool level) {
            HAL_GPIO_WritePin(GPIOA, GPIO_PIN_1, level ? GPIO_PIN_SET : GPIO_PIN_RESET);
        }

        static void setEnablePin(bool level) {
            HAL_GPIO_WritePin(GPIOA, GPIO_PIN_4, level ? GPIO_PIN_SET : GPIO_PIN_RESET);
        }

        static bool limitMinRaw() {
            return HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_0) == GPIO_PIN_RESET;
        }

        static bool limitMaxRaw() {
            return HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_1) == GPIO_PIN_RESET;
        }

        static bool estopRaw() {
            return HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_13) == GPIO_PIN_RESET;
        }

        /**
         * Drivers need roughly 5 us of stable DIR before the PUL edge.
         * HAL_Delay only resolves milliseconds, which would be a thousand times
         * too coarse, so this is a plain spin.
         */
        static void dirSetupDelay() {
            for (volatile int i = 0; i < 400; ++i) {
                __NOP();
            }
        }

    private:
        static inline TIM_HandleTypeDef *htim_ = nullptr;
    };
} // namespace stepctl
