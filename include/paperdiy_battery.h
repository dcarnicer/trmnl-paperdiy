#pragma once

// M5Stack PaperDIY battery: state of charge and the software low-battery cut-off.
//
// The M5PM1 has no fuel gauge, only VBAT in mV. The percentage comes from a
// discharge curve measured on this board by the PaperDIY CrossPoint port
// (github.com/dcarnicer/paperdiy-reader, BatteryMonitor.cpp): by default the
// 1500 mAh 604050 cell, or the 140 mAh kit cell with
// -D PAPERDIY_BATTERY_PROFILE_KIT. Both use 4.10 V as full: the cell rests
// there after a charge, 4.17 V is only the charger's constant voltage.

#ifdef BOARD_TRMNL_X_PAPERDIY

#include <stdint.h>

namespace paperdiy_battery {

  /// @brief Percentage to report for @p volts, measured at wake before the display
  ///        and Wi-Fi draw current. On USB it holds the last on-battery value while
  ///        charging and reports 100 once the charger is done; on battery it applies
  ///        the curve with a small hysteresis and remembers the result in NVS.
  /// @return 0-100, or -1 if the voltage read had failed
  int reportPercent(float volts);

  /// @brief True when the board runs on battery and @p volts is below the cut-off
  ///        (the curve's 0 %, ~3.43 V for the 1500 mAh cell). The PM1's own
  ///        hardware cut-off sits lower, at 3.0 V; see paperdiy_board.cpp.
  bool isEmpty(float volts);

} // namespace paperdiy_battery

#endif // BOARD_TRMNL_X_PAPERDIY
