#include <paperdiy_ota.h>

#ifdef BOARD_TRMNL_X_PAPERDIY

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <config.h>
#include <display.h>
#include <esp_ota_ops.h>
#include <globals.h>
#include <http_client.h>
#include <misc/clock.h>
#include <paperdiy_board.h>
#include <status_screens.h>
#include <trmnl_log.h>
#include <wifi_network.h>

// Arduino core hook (CONFIG_APP_ROLLBACK_ENABLE): don't mark a new image valid
// at boot; confirmRunningImage() does it once the image has done a full session.
extern "C" bool verifyRollbackLater() { return true; }

namespace {

  constexpr const char *KEY_LAST_CHECK = "pd_ota_chk";  // epoch of the last manifest check
  constexpr const char *KEY_PENDING = "pd_ota_pend";    // version just installed, until it boots
  constexpr const char *KEY_SKIP = "pd_ota_skip";       // version that was rolled back

#ifndef PAPERDIY_OTA_CHECK_INTERVAL_S
#define PAPERDIY_OTA_CHECK_INTERVAL_S (24 * 60 * 60)
#endif
  constexpr uint32_t CHECK_INTERVAL_S = PAPERDIY_OTA_CHECK_INTERVAL_S; // test builds shorten it
  constexpr int MIN_BATTERY_PERCENT = 50;
  // Smallest slot that can take the app (1.45 MB in 1.8.20-1) with room to grow
  constexpr uint32_t MIN_SLOT_BYTES = 2 * 1024 * 1024;

  struct Version {
    int major = -1, minor = -1, patch = -1, rev = -1;
    bool valid() const { return rev >= 0; }
    bool newerThan(const Version &o) const {
      if (major != o.major) return major > o.major;
      if (minor != o.minor) return minor > o.minor;
      if (patch != o.patch) return patch > o.patch;
      return rev > o.rev;
    }
  };

  Version parse(const String &s) {
    Version v;
    if (sscanf(s.c_str(), "%d.%d.%d-%d", &v.major, &v.minor, &v.patch, &v.rev) != 4) v = Version{};
    return v;
  }

  bool batteryAllowsUpdate() {
    if (paperdiy::usbStatus() == UsbStatus::CONNECTED) return true;
    const int percent = preferences.getInt("pd_batt_pct", -1); // paperdiy_battery's last reading
    return percent >= MIN_BATTERY_PERCENT;
  }

  bool hasUpdateSlot() {
    const esp_partition_t *next = esp_ota_get_next_update_partition(nullptr);
    if (!next) {
      Log_info(
        "PaperDIY OTA: no second app slot (single-slot partition table); reinstall the full image to enable updates");
      return false;
    }
    if (next->size < MIN_SLOT_BYTES) {
      Log_info("PaperDIY OTA: next slot %s is only %u KB", next->label, next->size / 1024);
      return false;
    }
    return true;
  }

  struct Manifest {
    String version;
    String appUrl;
  };

  bool fetchManifest(Manifest &out) {
    return withHttp(PAPERDIY_OTA_MANIFEST_URL, [&](HTTPClient *https, HttpError err) -> bool {
      if (err != HttpError::HTTPCLIENT_SUCCESS || !https) {
        Log_error("PaperDIY OTA: cannot connect for the manifest");
        return false;
      }
      https->setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS); // GitHub redirects release assets
      https->setTimeout(15000);
      const int code = https->GET();
      if (code != HTTP_CODE_OK) {
        Log_error("PaperDIY OTA: manifest GET returned %d", code);
        return false;
      }
      JsonDocument doc;
      const DeserializationError jerr = deserializeJson(doc, https->getString());
      if (jerr) {
        Log_error("PaperDIY OTA: bad manifest JSON: %s", jerr.c_str());
        return false;
      }
      out.version = doc["version"] | "";
      out.appUrl = doc["app_url"] | "";
#ifdef PAPERDIY_OTA_ALLOW_HTTP
      // Test builds only: a manifest and image served from the LAN
      const bool urlOk = out.appUrl.startsWith("https://") || out.appUrl.startsWith("http://");
#else
    const bool urlOk = out.appUrl.startsWith("https://");
#endif
      return out.version.length() > 0 && urlOk;
    });
  }

} // namespace

namespace paperdiy_ota {

  String version() { return String(FW_VERSION_STRING) + "-" + String(PAPERDIY_PORT_REVISION); }

  void noteUpdateOutcome() {
#ifdef PAPERDIY_OTA_TEST_CRASH
    // Test builds only: an image that never reaches sleep, to exercise the rollback
    Log_error("PaperDIY OTA: test crash");
    abort();
#endif
    const String pending = preferences.getString(KEY_PENDING, "");
    if (pending.isEmpty()) return;
    preferences.remove(KEY_PENDING);
    if (pending == version()) {
      Log_info("PaperDIY OTA: now running %s", pending.c_str());
      return;
    }
    // Back on the old image: the bootloader rolled the new one back
    Log_error("PaperDIY OTA: %s did not come up, back on %s; it will not be installed again", pending.c_str(),
              version().c_str());
    preferences.putString(KEY_SKIP, pending);
  }

  void confirmRunningImage() {
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t state;
    if (!running || esp_ota_get_state_partition(running, &state) != ESP_OK) return;
    if (state != ESP_OTA_IMG_PENDING_VERIFY) return;
    const esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
    Log_info("PaperDIY OTA: %s confirmed (%s)", version().c_str(), esp_err_to_name(err));
  }

  void updateIfDue() {
    if (paperdiy::underLauncher()) return; // Launcher's catalog updates it

    const uint32_t now = systemClock().getTime();
    const uint32_t last = preferences.getUInt(KEY_LAST_CHECK, 0);
    if (now == 0) {
      Log_info("PaperDIY OTA: clock not set, skipping the update check");
      return;
    }
    if (last != 0 && now >= last && now - last < CHECK_INTERVAL_S) return;
    if (!hasUpdateSlot()) return;
    if (!batteryAllowsUpdate()) {
      Log_info("PaperDIY OTA: battery under %d %%, checking later", MIN_BATTERY_PERCENT);
      return;
    }

    // The display session switches Wi-Fi off once the image is downloaded
    if (!ensureWifiConnected()) {
      Log_error("PaperDIY OTA: no Wi-Fi for the update check");
      return;
    }
    preferences.putUInt(KEY_LAST_CHECK, now);
    Manifest manifest;
    if (!fetchManifest(manifest)) return;

    const Version latest = parse(manifest.version);
    const Version current = parse(version());
    Log_info("PaperDIY OTA: running %s, latest %s", version().c_str(), manifest.version.c_str());
    if (!latest.valid() || !latest.newerThan(current)) return;
    if (manifest.version == preferences.getString(KEY_SKIP, "")) {
      Log_info("PaperDIY OTA: %s was rolled back before, skipping it", manifest.version.c_str());
      return;
    }

    // FirmwareUpdateService downloads into the next slot and sets it to boot;
    // it also holds off for 24 h after a failed attempt.
    if (!firmwareUpdateService.isUpdateDue(true, manifest.appUrl)) return;
    showMessageWithLogo(FW_UPDATE);
    const FirmwareUpdateResult result = firmwareUpdateService.performUpdate();
    if (result.updated) {
      preferences.putString(KEY_PENDING, manifest.version);
      showMessageWithLogo(FW_UPDATE_SUCCESS);
      preferences.end();
      ESP.restart();
    }
    if (result.failureMessage != NONE) {
      showMessageWithLogo(result.failureMessage);
      refreshInterval.applyFailedUpdate();
    }
  }

} // namespace paperdiy_ota

#endif // BOARD_TRMNL_X_PAPERDIY
