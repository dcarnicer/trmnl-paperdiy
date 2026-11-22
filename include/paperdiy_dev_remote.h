#pragma once

// PaperDIY dev remote: drives the app over the USB cable for smoke tests and
// UI checks, without anyone at the buttons. Only in builds with
// -D PAPERDIY_DEV_REMOTE (the TRMNL_X_PAPERDIY_dev env); never in a release,
// since anything on the USB cable could press its buttons and change its server.
//
// When a wake is about to end (any path into deep sleep or the PM1 power-off),
// the board listens on the serial port. A "CMD:" line, sent during the wake or
// within a short window at its end, holds it awake in a command loop:
//
//   CMD:PING          REMOTE:PONG with the version and the session counter
//   CMD:HOLD          stay in the loop (a host sends it to catch the board)
//   CMD:STATUS        battery, power, Wi-Fi, server, panel and memory
//   CMD:SHOT          the framebuffer as hex rows (#shot / #r / #end)
//   CMD:REFRESH       restart as if button A had been pressed (BUTTON:SHORT)
//   CMD:BUTTON:SHORT|DOUBLE|LONG   restart as a button wake with that press
//   CMD:WAKE:TIMER    restart as a timer wake (no logo, errors retried quietly)
//   CMD:RESTART       restart as a cold boot (the PM1 power button's click)
//   CMD:SERVER[:URL]  show or set the API URL in NVS; "-" restores the default
//   CMD:FREE          the next wake sleeps normally instead of holding
//   CMD:SLEEP         leave the loop and sleep now
//
// Each restart keeps the hold (unless FREE was sent), so the next wake ends in
// the loop again. The loop never holds on battery, and gives up after 15
// minutes without a command. See docs/paperdiy-dev.md and
// scripts/paperdiy/remote.py.

#if defined(BOARD_TRMNL_X_PAPERDIY) && defined(PAPERDIY_DEV_REMOTE)

#include <button.h>
#include <esp_sleep.h>

namespace paperdiy_dev_remote {

  /// @brief At boot, after the wake cause is read: a REFRESH, BUTTON or WAKE
  ///        command replaces it with the wake it simulates.
  /// @return true if @p reason was replaced
  bool injectedWake(esp_sleep_wakeup_cause_t *reason);

  /// @brief The button press a BUTTON command simulated, or @p read_buttons()
  ///        when there is none.
  ButtonPressResult injectedPressOr(ButtonPressResult (*read_buttons)());

  /// @brief Before the board sleeps: listens for commands and, when held, runs
  ///        the command loop. Returns to let the board sleep; restart commands
  ///        do not return.
  void beforeSleep();

} // namespace paperdiy_dev_remote

#endif // BOARD_TRMNL_X_PAPERDIY && PAPERDIY_DEV_REMOTE
