#pragma once

// M5Stack PaperDIY (K159) board support.
//
// The PaperDIY wires its ED047TC1 panel exactly like the M5PaperS3, so FastEPD's
// BB_PANEL_M5PAPERS3 drives it unchanged. What differs is the M5PM1 PMIC
// (I2C SDA41/SCL42, address 0x6E):
//   - PM1 GPIO2 gates an extra EPD supply rail that must be up before the panel
//     is powered (on top of the PaperS3-style PWR pin, GPIO46);
//   - the PM1 owns charging (CHG_EN), the 5 V DCDC, battery/VIN sensing and the
//     power button; its PWR_CFG register clears on every PM1 reset;
//   - PM1 GPIO3 is the charger's CHG_STAT line (active low).
// GPIO3 on the ESP32 is user button B, so the PaperS3's ADC battery pin must
// never be read here. Button A (GPIO4, active low) is the TRMNL button.

#ifdef BOARD_TRMNL_X_PAPERDIY

#include <hardware_types.h>
#include <stdint.h>

namespace paperdiy {

/// @brief Starts the PM1 I2C bus and applies the boot power policy (charger and
///        5 V DCDC on, CHG_STAT as input). Idempotent; every other call runs it.
/// @return false if the PM1 did not answer
bool begin();

/// @brief Switches the PM1 EPD rail (PM1 GPIO2).
bool setEpdRail(bool on);

/// @brief Battery voltage from the PM1 ADC.
/// @return volts, or a negative value if the read failed
float batteryVolts();

/// @brief USB presence from the PM1 power-source bitmap.
UsbStatus usbStatus();

/// @brief Charge state from the CHG_STAT line on PM1 GPIO3.
ChargingStatus chargingStatus();

/// @brief Cuts the EPD rail before deep sleep.
void prepareForDeepSleep();

/// PM1 wake-source flags, as returned by pm1WakeSource().
constexpr uint8_t WAKE_TIMER = 1 << 0;
constexpr uint8_t WAKE_VIN = 1 << 1;
constexpr uint8_t WAKE_POWER_BUTTON = 1 << 2;

/// @brief Why the PM1 last powered the board on (WAKE_* flags). Read and cleared
///        on the first call, which also stops a PM1 timer left armed; later
///        calls return the same value.
uint8_t pm1WakeSource();

/// @brief True when the board runs on its battery alone (no USB or 5 V input).
bool onBattery();

/// @brief Powers the whole board off through the PM1, which turns it back on
///        after @p seconds (0: only the power button or USB turn it on). The
///        board then cold-boots, so RTC memory is lost.
/// @return only on failure (false); the caller should fall back to deep sleep
bool powerOffFor(uint32_t seconds);

/// @brief Logs the PM1 firmware revision, power source, PWR_CFG, LVP and VIN.
void logPowerState();

/// @brief True when M5Launcher is installed (its resident partition has the
///        app "test" subtype). Under Launcher this app runs from a dynamic slot.
bool underLauncher();

/// @brief Logs Launcher's boot settings (NVS namespace "launcher") and warns when
///        a PM1 power-on would show Launcher's screen before this app.
void logLauncher();

} // namespace paperdiy

#endif // BOARD_TRMNL_X_PAPERDIY
