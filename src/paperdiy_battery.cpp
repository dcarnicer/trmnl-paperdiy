#include <paperdiy_battery.h>

#ifdef BOARD_TRMNL_X_PAPERDIY

#include <Arduino.h>
#include <globals.h>
#include <paperdiy_board.h>
#include <trmnl_log.h>

namespace {

  // mV at 0 %, 10 %, ... 100 %, from paperdiy-reader's BatteryMonitor.cpp.
#ifdef PAPERDIY_BATTERY_PROFILE_KIT
  // The 140 mAh cell shipped with the kit.
  constexpr uint16_t NOTCH_MV[11] = {3450, 3680, 3740, 3770, 3790, 3820, 3870, 3920, 3980, 4060, 4100};
#else
  // The 1500 mAh 604050 cell, measured over a 45.4 h discharge (30 s/page, no Wi-Fi).
  constexpr uint16_t NOTCH_MV[11] = {3431, 3516, 3603, 3680, 3778, 3846, 3894, 3980, 4022, 4064, 4100};
#endif

  // A notch change must clear the boundary by this much (sampler noise), as in the reader
  constexpr uint16_t HYSTERESIS_MV = 8;

  constexpr const char *KEY_PERCENT = "pd_batt_pct"; // last on-battery percentage

  int percentFromMillivolts(uint16_t mv) {
    if (mv >= NOTCH_MV[10]) return 100;
    // Round at the midpoint of each segment, so a cell resting just below full reads 100 %
    for (int i = 10; i > 0; --i) {
      if (mv >= (NOTCH_MV[i - 1] + NOTCH_MV[i]) / 2) return i * 10;
    }
    return 0;
  }

  int percentWithHysteresis(uint16_t mv, int previous) {
    const int notch = percentFromMillivolts(mv);
    if (previous < 0 || previous > 100 || notch == previous) return notch;
    const int biased = static_cast<int>(mv) + (notch > previous ? -HYSTERESIS_MV : HYSTERESIS_MV);
    return percentFromMillivolts(static_cast<uint16_t>(max(biased, 0))) == notch ? notch : previous;
  }

} // namespace

namespace paperdiy_battery {

  int reportPercent(float volts) {
    if (volts <= 0.0f) return -1;
    const uint16_t mv = static_cast<uint16_t>(volts * 1000.0f + 0.5f);
    const int saved = preferences.getInt(KEY_PERCENT, -1);

    if (paperdiy::usbStatus() == UsbStatus::CONNECTED) {
      // The terminal voltage runs high under charge current: it says nothing about the charge
      if (paperdiy::chargingStatus() == ChargingStatus::NOT_CHARGING) return 100; // charger done
      return saved >= 0 ? saved : percentFromMillivolts(mv);
    }

    const int percent = percentWithHysteresis(mv, saved);
    if (percent != saved) preferences.putInt(KEY_PERCENT, percent);
    Log_info("PaperDIY: battery %u mV -> %d %%", mv, percent);
    return percent;
  }

  bool isEmpty(float volts) {
    if (volts <= 0.0f || !paperdiy::onBattery()) return false;
    return static_cast<uint16_t>(volts * 1000.0f) < NOTCH_MV[0];
  }

} // namespace paperdiy_battery

#endif // BOARD_TRMNL_X_PAPERDIY
