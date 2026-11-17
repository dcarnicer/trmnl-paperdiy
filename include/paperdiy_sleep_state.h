#pragma once

// M5Stack PaperDIY: keeps the deep-sleep state across a PM1 power-off.
//
// On battery the PaperDIY sleeps by switching itself off through the M5PM1,
// whose timer powers it back on (tens of µA instead of the ESP32's deep sleep).
// The board then cold-boots and loses RTC memory, so the RTC_DATA_ATTR state the
// firmware relies on (the image on screen, the partial-update counter, the wake
// statistics) goes to NVS just before the power-off and comes back at the next
// boot.

#ifdef BOARD_TRMNL_X_PAPERDIY

namespace paperdiy_sleep_state {

/// @brief Saves the RTC state to NVS. Needs the preferences open.
void save();

/// @brief Restores the RTC state saved by save(), once, and deletes it.
///        Needs the preferences open. No-op when nothing was saved.
void restore();

} // namespace paperdiy_sleep_state

#endif // BOARD_TRMNL_X_PAPERDIY
