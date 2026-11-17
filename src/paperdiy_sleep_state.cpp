#include <paperdiy_sleep_state.h>

#ifdef BOARD_TRMNL_X_PAPERDIY

#include <Arduino.h>
#include <globals.h>
#include <string.h>
#include <trmnl_log.h>

#include "displayed_image.h"

namespace {

constexpr const char *KEY = "pd_sleep"; // NVS keys are 15 characters at most
constexpr uint8_t VERSION = 1;

struct SleepState {
  uint8_t version;
  uint8_t need_to_refresh_display;
  bool used_cached_image;
  bool can_do_partial;
  bool image_covered;
  int32_t prev_wake_time;
  int32_t last_wifi_rssi;
  int32_t update_count;
  char image[36];
};

} // namespace

namespace paperdiy_sleep_state {

void save() {
  SleepState state = {};
  state.version = VERSION;
  state.need_to_refresh_display = need_to_refresh_display;
  state.used_cached_image = bUsedCachedImage;
  state.can_do_partial = bCanDoPartial;
  state.image_covered = DisplayedImage::isCovered();
  state.prev_wake_time = iPrevWakeTime;
  state.last_wifi_rssi = last_wifi_rssi;
  state.update_count = iUpdateCount;
  strncpy(state.image, DisplayedImage::get(), sizeof(state.image) - 1);
  if (preferences.putBytes(KEY, &state, sizeof(state)) != sizeof(state)) {
    Log_error("PaperDIY: sleep state not saved");
  }
}

void restore() {
  if (!preferences.isKey(KEY)) return;
  SleepState state = {};
  const size_t len = preferences.getBytes(KEY, &state, sizeof(state));
  preferences.remove(KEY);
  if (len != sizeof(state) || state.version != VERSION) {
    Log_error("PaperDIY: saved sleep state ignored (%u bytes, version %u)", (unsigned)len, state.version);
    return;
  }
  state.image[sizeof(state.image) - 1] = '\0';
  need_to_refresh_display = state.need_to_refresh_display;
  bUsedCachedImage = state.used_cached_image;
  bCanDoPartial = state.can_do_partial;
  iPrevWakeTime = state.prev_wake_time;
  last_wifi_rssi = state.last_wifi_rssi;
  iUpdateCount = state.update_count;
  if (state.image[0] != '\0') {
    DisplayedImage::remember(state.image);
    if (state.image_covered) DisplayedImage::cover();
  }
  Log_info("PaperDIY: sleep state restored (image %s%s)", state.image[0] ? state.image : "none",
           state.image_covered ? ", covered" : "");
}

} // namespace paperdiy_sleep_state

#endif // BOARD_TRMNL_X_PAPERDIY
