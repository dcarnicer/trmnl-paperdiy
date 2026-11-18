#include <paperdiy_board.h>

#ifdef BOARD_TRMNL_X_PAPERDIY

#include <Arduino.h>
#include <Preferences.h>
#include <Wire.h>
#include <driver/rtc_io.h>
#include <esp_partition.h>
#include <esp_sleep.h>
#include <trmnl_log.h>

// Register map and timings follow the M5PM1 datasheet and M5Stack's M5PM1 library
// (MIT); the boot power policy matches the PaperDIY Reader port, which validated
// it on hardware.

namespace {

  constexpr uint8_t PM1_ADDR = 0x6E;
  constexpr int PM1_SDA = 41;
  constexpr int PM1_SCL = 42;
  constexpr uint32_t PM1_I2C_HZ = 100000;

  constexpr uint8_t REG_SW_REV = 0x03;     // PM1 firmware revision
  constexpr uint8_t REG_PWR_SRC = 0x04;    // bitmap: [0] 5VIN, [1] 5VINOUT, [2] battery
  constexpr uint8_t REG_WAKE_SRC = 0x05;   // why the PM1 powered the board on; write 0 to clear
  constexpr uint8_t REG_PWR_CFG = 0x06;    // clears on PM1 reset/shutdown
  constexpr uint8_t REG_BATT_LVP = 0x08;   // 2000 + n * 7.81 mV
  constexpr uint8_t REG_I2C_CFG = 0x09;    // [3:0] idle-sleep timeout, 0 = never
  constexpr uint8_t REG_WDT_CNT = 0x0A;    // 0 = PM1 watchdog off
  constexpr uint8_t REG_SYS_CMD = 0x0C;    // 0xA0 key | command
  constexpr uint8_t REG_GPIO_MODE = 0x10;  // 1 = output
  constexpr uint8_t REG_GPIO_OUT = 0x11;
  constexpr uint8_t REG_GPIO_IN = 0x12;
  constexpr uint8_t REG_GPIO_DRV = 0x13;   // 0 = push-pull
  constexpr uint8_t REG_GPIO_PULL0 = 0x14; // 2 bits per pin, 01 = pull-up
  constexpr uint8_t REG_GPIO_FUNC0 = 0x16; // 2 bits per pin, 00 = plain GPIO
  constexpr uint8_t REG_VBAT_L = 0x22;     // battery millivolts, 16-bit little endian
  constexpr uint8_t REG_VIN_L = 0x24;      // USB input millivolts, 16-bit little endian
  constexpr uint8_t REG_TIM_CNT0 = 0x38;   // 31-bit timer in seconds, little endian (0x38-0x3B)
  constexpr uint8_t REG_TIM_CFG = 0x3C;    // [3] start, [2:0] action on expiry
  constexpr uint8_t REG_TIM_KEY = 0x3D;    // 0xA5 loads the timer
  constexpr uint8_t REG_NEO_CFG = 0x50;    // 0 = PM1 NeoPixel engine off

  constexpr uint8_t SYS_CMD_SHUTDOWN = 0xA0 | 0x01;
  constexpr uint8_t TIM_START = 1 << 3;
  constexpr uint8_t TIM_ACTION_POWER_ON = 0x03;
  constexpr uint8_t TIM_RELOAD_KEY = 0xA5;
  constexpr uint32_t TIM_MAX_SECONDS = 0x7FFFFFFF / 10; // M5PM1 library limit (~6.8 years)

  constexpr uint8_t CHG_EN = 1 << 0;
  constexpr uint8_t DCDC_EN = 1 << 1;
  constexpr uint8_t LDO_EN = 1 << 2;
  constexpr uint8_t BOOST_EN = 1 << 3;
  constexpr uint8_t LED_R_EN = 1 << 4;

  constexpr uint8_t PWR_SRC_5VIN = 1 << 0;
  constexpr uint8_t PWR_SRC_5VINOUT = 1 << 1;
  constexpr uint8_t PWR_SRC_BATTERY = 1 << 2;

  constexpr uint8_t PM1_GPIO2 = 1 << 2; // EPD rail enable
  constexpr uint8_t PM1_GPIO3 = 1 << 3; // CHG_STAT, active low

  // Hardware low-voltage cut-off. At 3.4 V the PM1 cut the power during the first
  // fast refresh with the battery resting at 4.17 V, every time (the board then
  // stayed off until a long press of the power button). Lowering only this value
  // fixed it, so the PM1 apparently acts on the VBAT sag under the refresh load.
  // 3.0 V only catches a really empty cell; the cell's own protection sits lower.
  constexpr uint16_t BATTERY_LVP_MV = 3000;

  constexpr gpio_num_t BUTTON_A = GPIO_NUM_4; // TRMNL button, active low

  // The PM1 is a PY32 MCU emulating an I2C slave in firmware. M5Stack's driver
  // pauses after every transaction; without it the PM1 serves stale data.
  constexpr uint32_t XFER_DELAY_US = 500;

  bool busStarted = false;
  bool policyApplied = false;

  bool writeReg(uint8_t reg, uint8_t value) {
    Wire.beginTransmission(PM1_ADDR);
    Wire.write(reg);
    Wire.write(value);
    if (Wire.endTransmission() != 0) return false;
    delayMicroseconds(XFER_DELAY_US);
    return true;
  }

  bool readBytes(uint8_t reg, uint8_t *data, uint8_t len) {
    Wire.beginTransmission(PM1_ADDR);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return false;
    delayMicroseconds(XFER_DELAY_US);
    if (Wire.requestFrom(PM1_ADDR, len) != len) return false;
    for (uint8_t i = 0; i < len; ++i)
      data[i] = Wire.read();
    return true;
  }

  bool readReg(uint8_t reg, uint8_t *value) { return readBytes(reg, value, 1); }

  bool updateReg(uint8_t reg, uint8_t clearMask, uint8_t setMask) {
    uint8_t value = 0;
    if (!readReg(reg, &value)) return false;
    return writeReg(reg, static_cast<uint8_t>((value & ~clearMask) | setMask));
  }

  void startBus() {
    if (busStarted) return;
    Wire.begin(PM1_SDA, PM1_SCL, PM1_I2C_HZ);
    Wire.setTimeOut(4);
    busStarted = true;
    // Keep the PM1 on the bus between transactions and its watchdog off.
    writeReg(REG_I2C_CFG, 0x00);
    writeReg(REG_WDT_CNT, 0x00);
  }

} // namespace

namespace paperdiy {

  bool begin() {
    startBus();
    if (policyApplied) return true;

    // Charger and 5 V DCDC (panel supply) on. The PaperDIY has no Grove boost or
    // RGB rail, so BOOST_EN, LDO_EN and LED_R_EN stay off.
    bool ok = updateReg(REG_PWR_CFG, BOOST_EN | LDO_EN | LED_R_EN, CHG_EN | DCDC_EN);
    ok = writeReg(REG_NEO_CFG, 0x00) && ok;
    // Hardware cut-off at 3.0 V: only a really empty cell trips it (see BATTERY_LVP_MV;
    // the software cut-off in paperdiy_battery stops earlier). Best effort.
    writeReg(REG_BATT_LVP, static_cast<uint8_t>((BATTERY_LVP_MV - 2000) * 100 / 781));

    // GPIO2: push-pull output for the EPD rail, left as it is until the display asks.
    ok = updateReg(REG_GPIO_FUNC0, 0x03 << 4, 0) && ok;
    ok = updateReg(REG_GPIO_MODE, 0, PM1_GPIO2) && ok;
    ok = updateReg(REG_GPIO_DRV, PM1_GPIO2, 0) && ok;
    // GPIO3: CHG_STAT input with pull-up.
    ok = updateReg(REG_GPIO_FUNC0, 0x03 << 6, 0) && ok;
    ok = updateReg(REG_GPIO_MODE, PM1_GPIO3, 0) && ok;
    ok = updateReg(REG_GPIO_PULL0, 0x03 << 6, 0x01 << 6) && ok;

    if (!ok) {
      Log_error("PaperDIY: M5PM1 did not accept the power policy");
      return false;
    }
    policyApplied = true;
    Log_info("PaperDIY: M5PM1 ready");
    return true;
  }

  bool setEpdRail(bool on) {
    begin();
    const bool ok = updateReg(REG_GPIO_OUT, PM1_GPIO2, on ? PM1_GPIO2 : 0);
    if (!ok) {
      Log_error("PaperDIY: EPD rail %s failed", on ? "on" : "off");
    } else if (on) {
      delay(10); // let the rail settle before FastEPD raises the panel supply
    }
    return ok;
  }

  float batteryVolts() {
    begin();
    uint8_t raw[2] = {0, 0};
    if (!readBytes(REG_VBAT_L, raw, sizeof(raw))) {
      Log_error("PaperDIY: battery read failed");
      return -1.0f;
    }
    const uint16_t mv = static_cast<uint16_t>(raw[0] | (raw[1] << 8));
    Log_info("PaperDIY: battery %u mV", mv);
    return mv / 1000.0f;
  }

  UsbStatus usbStatus() {
    begin();
    uint8_t sources = 0;
    if (!readReg(REG_PWR_SRC, &sources)) return UsbStatus::UNKNOWN;
    return (sources & (PWR_SRC_5VIN | PWR_SRC_5VINOUT)) ? UsbStatus::CONNECTED : UsbStatus::DISCONNECTED;
  }

  ChargingStatus chargingStatus() {
    begin();
    uint8_t gpio = 0;
    if (!readReg(REG_GPIO_IN, &gpio)) return ChargingStatus::UNKNOWN;
    return (gpio & PM1_GPIO3) == 0 ? ChargingStatus::CHARGING : ChargingStatus::NOT_CHARGING;
  }

  uint8_t pm1WakeSource() {
    static bool read = false;
    static uint8_t source = 0;
    if (read) return source;
    read = true;
    begin();
    if (!readReg(REG_WAKE_SRC, &source)) {
      Log_error("PaperDIY: wake source read failed");
      source = 0;
    }
    source &= 0x7F;
    // Clear it, and stop a timer left armed by the last power-off
    writeReg(REG_WAKE_SRC, 0x00);
    writeReg(REG_TIM_CFG, 0x00);
    writeReg(REG_TIM_KEY, TIM_RELOAD_KEY);
    Log_info("PaperDIY: PM1 wake source 0x%02X", source);
    return source;
  }

  bool onBattery() {
    begin();
    uint8_t sources = 0;
    if (!readReg(REG_PWR_SRC, &sources)) return false; // unknown: keep the safe deep-sleep path
    return (sources & (PWR_SRC_5VIN | PWR_SRC_5VINOUT)) == 0 && (sources & PWR_SRC_BATTERY);
  }

  bool powerOffFor(uint32_t seconds) {
    begin();
    setEpdRail(false);
    if (seconds > TIM_MAX_SECONDS) seconds = TIM_MAX_SECONDS;
    bool ok = true;
    if (seconds > 0) {
      const uint8_t count[4] = {
          static_cast<uint8_t>(seconds), static_cast<uint8_t>(seconds >> 8), static_cast<uint8_t>(seconds >> 16),
          static_cast<uint8_t>((seconds >> 24) & 0x7F)};
      for (uint8_t i = 0; i < 4 && ok; ++i)
        ok = writeReg(REG_TIM_CNT0 + i, count[i]);
      ok = ok && writeReg(REG_TIM_CFG, TIM_START | TIM_ACTION_POWER_ON);
      ok = ok && writeReg(REG_TIM_KEY, TIM_RELOAD_KEY);
      if (!ok) {
        Log_error("PaperDIY: PM1 timer not armed, staying in deep sleep");
        return false;
      }
    }
    Log_info("PaperDIY: PM1 power-off, back on in %lu s", static_cast<unsigned long>(seconds));
    Serial.flush();
    delay(120); // M5Stack's driver waits this long before a system command
    writeReg(REG_SYS_CMD, SYS_CMD_SHUTDOWN);
    delay(1000);
    // Still running: the PM1 kept the power on (USB plugged in meanwhile?)
    Log_error("PaperDIY: PM1 did not power off");
    writeReg(REG_TIM_CFG, 0x00);
    writeReg(REG_TIM_KEY, TIM_RELOAD_KEY);
    return false;
  }

  void logPowerState() {
    begin();
    uint8_t rev = 0, src = 0, cfg = 0, lvp = 0;
    uint8_t vin[2] = {0, 0};
    readReg(REG_SW_REV, &rev);
    readReg(REG_PWR_SRC, &src);
    readReg(REG_PWR_CFG, &cfg);
    readReg(REG_BATT_LVP, &lvp);
    readBytes(REG_VIN_L, vin, sizeof(vin));
    Log_info("PaperDIY: PM1 fw 0x%02X, source 0x%02X, PWR_CFG 0x%02X, LVP %u mV, VIN %u mV", rev, src, cfg,
             2000 + lvp * 781 / 100, static_cast<unsigned>((vin[0] | (vin[1] << 8)) & 0x0FFF));
  }

  bool underLauncher() {
    // M5Launcher keeps its resident app in a partition with the "test" subtype at 0x10000
    static const bool present =
      esp_partition_find_first(ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_TEST, nullptr) != nullptr;
    return present;
  }

  void logLauncher() {
    if (!underLauncher()) return;
    // The same keys Launcher's bootloader reads on every reset
    Preferences launcher;
    if (!launcher.begin("launcher", true)) {
      Log_info("PaperDIY: M5Launcher detected (no settings saved yet)");
      Log_info("PaperDIY: set Launcher's 'GPIO on boot starts Launcher' to A so wakes skip its screen");
      return;
    }
    const uint8_t ddlb = launcher.getUChar("DDLB", 0);
    const int32_t key = launcher.getInt("LauncherOnKey", -1);
    const uint8_t level = launcher.getUChar("LauncherKeyLvl", 0);
    launcher.end();
    Log_info("PaperDIY: M5Launcher detected: DDLB=%u LauncherOnKey=%ld LauncherKeyLvl=%u", ddlb, static_cast<long>(key),
             level);
    if (key < 0) {
      // Each PM1 power-on would go through Launcher's boot screen (an extra refresh per wake)
      Log_info("PaperDIY: set Launcher's 'GPIO on boot starts Launcher' to A so wakes skip its screen");
    }
  }

  void prepareForDeepSleep() {
    setEpdRail(false);
    // In deep sleep the pad leaves the digital domain, so the pull from pinMode() no
    // longer applies and button A floats; ext0 then reads it as pressed and wakes the
    // board at once. Hold it high with the RTC pull-up and keep that domain powered.
    esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_PERIPH, ESP_PD_OPTION_ON);
    rtc_gpio_pulldown_dis(BUTTON_A);
    rtc_gpio_pullup_en(BUTTON_A);
  }

} // namespace paperdiy

#endif // BOARD_TRMNL_X_PAPERDIY
