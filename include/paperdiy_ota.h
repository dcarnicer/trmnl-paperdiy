#pragma once

// M5Stack PaperDIY firmware updates from the port's GitHub releases.
//
// TRMNL servers offer updates for the model the device reports, which here is
// the PaperS3's, so the server's update_firmware is ignored. Instead, at most
// once a day the firmware reads latest.json from the latest GitHub release
// and, when it names a newer version, downloads the app image into the other
// OTA slot and restarts into it.
//
// - Only standalone, with the two-slot partition table (boards/
//   paperdiy_partitions.csv). Under M5Launcher the app sits in a slot Launcher
//   sized for it, with no room for a second one: Launcher's own catalog
//   (M5Burner) updates it.
// - Only on USB, or on battery at 50 % or more.
// - The bootloader rolls back to the previous image if the new one does not
//   reach sleep on its first boot (a crash or reset loop). A version that was
//   rolled back is not downloaded again.

#ifdef BOARD_TRMNL_X_PAPERDIY

// Port revision N of release X.Y.Z-N: X.Y.Z is TRMNL's FW_VERSION_STRING.
// scripts/paperdiy/check_release_version.py checks both against the tag.
// Bump it whenever the port's firmware changes, so a release tag is never reused
// and an installed board sees the update.
#define PAPERDIY_PORT_REVISION 3

#ifndef PAPERDIY_OTA_MANIFEST_URL
#define PAPERDIY_OTA_MANIFEST_URL "https://github.com/dcarnicer/trmnl-paperdiy/releases/latest/download/latest.json"
#endif

#include <Arduino.h>

namespace paperdiy_ota {

  /// @brief This build's release version, "X.Y.Z-N".
  String version();

  /// @brief At boot, once preferences are open: if the last update did not come
  ///        up (the bootloader rolled it back), remember that version to skip it.
  void noteUpdateOutcome();

  /// @brief Before sleeping or powering off: the running image has worked, so
  ///        cancel the bootloader's rollback if it was pending verification.
  void confirmRunningImage();

  /// @brief After the screen is shown: once a day, check GitHub and install a
  ///        newer release. Restarts the board if it installed one.
  void updateIfDue();

} // namespace paperdiy_ota

#endif // BOARD_TRMNL_X_PAPERDIY
