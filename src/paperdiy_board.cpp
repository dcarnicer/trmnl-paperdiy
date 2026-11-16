#include <paperdiy_board.h>

#ifdef BOARD_TRMNL_X_PAPERDIY

#include <Arduino.h>
#include <Wire.h>
#include <driver/rtc_io.h>
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

constexpr uint8_t REG_PWR_SRC = 0x04;    // bitmap: [0] 5VIN, [1] 5VINOUT, [2] battery
constexpr uint8_t REG_PWR_CFG = 0x06;    // clears on PM1 reset/shutdown
constexpr uint8_t REG_BATT_LVP = 0x08;   // 2000 + n * 7.81 mV
constexpr uint8_t REG_I2C_CFG = 0x09;    // [3:0] idle-sleep timeout, 0 = never
constexpr uint8_t REG_WDT_CNT = 0x0A;    // 0 = PM1 watchdog off
constexpr uint8_t REG_GPIO_MODE = 0x10;  // 1 = output
constexpr uint8_t REG_GPIO_OUT = 0x11;
constexpr uint8_t REG_GPIO_IN = 0x12;
constexpr uint8_t REG_GPIO_DRV = 0x13;   // 0 = push-pull
constexpr uint8_t REG_GPIO_PULL0 = 0x14; // 2 bits per pin, 01 = pull-up
constexpr uint8_t REG_GPIO_FUNC0 = 0x16; // 2 bits per pin, 00 = plain GPIO
constexpr uint8_t REG_VBAT_L = 0x22;     // battery millivolts, 16-bit little endian
constexpr uint8_t REG_NEO_CFG = 0x50;    // 0 = PM1 NeoPixel engine off

constexpr uint8_t CHG_EN = 1 << 0;
constexpr uint8_t DCDC_EN = 1 << 1;
constexpr uint8_t LDO_EN = 1 << 2;
constexpr uint8_t BOOST_EN = 1 << 3;
constexpr uint8_t LED_R_EN = 1 << 4;

constexpr uint8_t PWR_SRC_5VIN = 1 << 0;
constexpr uint8_t PWR_SRC_5VINOUT = 1 << 1;

constexpr uint8_t PM1_GPIO2 = 1 << 2; // EPD rail enable
constexpr uint8_t PM1_GPIO3 = 1 << 3; // CHG_STAT, active low

constexpr uint16_t BATTERY_LVP_MV = 3400;

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
  for (uint8_t i = 0; i < len; ++i) data[i] = Wire.read();
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
  // Low-voltage cut-off at 3.4 V: off the tail of the Li-ion curve, with margin
  // for the sag of an e-ink refresh. Best effort.
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
