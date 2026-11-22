#if defined(BOARD_TRMNL_X_PAPERDIY) && defined(PAPERDIY_DEV_REMOTE)

#include <Arduino.h>
#include <FastEPD.h>
#include <WiFi.h>
#include <WifiCaptive.h>
#include <config.h>
#include <esp_attr.h>
#include <globals.h>
#include <paperdiy_battery.h>
#include <paperdiy_board.h>
#include <paperdiy_dev_remote.h>
#include <paperdiy_ota.h>
#include <paperdiy_remote_command.h>
#include <sleep_session.h>
#include <trmnl_log.h>

#include "messages.h"

extern FASTEPD bbep; // display.cpp

namespace paperdiy_dev_remote {

  namespace {

    using paperdiy_remote::Command;

    // How long the end of a wake waits for a host that has not sent anything yet
    constexpr uint32_t LISTEN_MS = 1500;
    // A held board with no command for this long sleeps, so a host that went away
    // does not keep it awake
    constexpr uint32_t IDLE_MS = 15 * 60 * 1000;
    // How often the loop checks that USB is still there (a PM1 read)
    constexpr uint32_t BATTERY_CHECK_MS = 2000;
    // The longest framebuffer row a dump sends (960 px at 4 bpp)
    constexpr size_t MAX_ROW_BYTES = 480;

    enum class Wake : uint8_t { None, Timer, Button };

    // Survives ESP.restart() and deep sleep, not a power-on or a PM1 power-off
    struct State {
      uint32_t magic;
      uint8_t hold;  // the next end of a wake runs the command loop
      uint8_t wake;  // Wake to simulate on the next boot
      uint8_t press; // ButtonPressResult + 1 to simulate, 0 for none
      uint8_t reserved;
      uint32_t seq; // command loops run since power-on
    };
    constexpr uint32_t MAGIC = 0x50445231; // "PDR1"
    RTC_NOINIT_ATTR State rtc_state;

    State &state() {
      if (rtc_state.magic != MAGIC) {
        memset(&rtc_state, 0, sizeof(rtc_state));
        rtc_state.magic = MAGIC;
      }
      return rtc_state;
    }

    // Collects one line from the serial port; longer lines are dropped whole
    class LineReader {
    public:
      // True once a whole line is in line()
      bool poll() {
        while (Serial.available() > 0) {
          const int c = Serial.read();
          if (c < 0) break;
          if (c == '\n') {
            const bool complete = !overflow_;
            buffer_[len_] = '\0';
            len_ = 0;
            overflow_ = false;
            if (complete) {
              paperdiy_remote::trimLine(buffer_);
              return true;
            }
            continue;
          }
          if (len_ + 1 < sizeof(buffer_)) {
            buffer_[len_++] = (char)c;
          } else {
            overflow_ = true;
          }
        }
        return false;
      }
      const char *line() const { return buffer_; }

    private:
      char buffer_[200] = {};
      size_t len_ = 0;
      bool overflow_ = false;
    };

    // zlib's CRC-32, so the host can check a dump with zlib.crc32()
    uint32_t crc32Update(uint32_t crc, const uint8_t *data, size_t len) {
      crc = ~crc;
      for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++) {
          crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
        }
      }
      return ~crc;
    }

    // A STATUS value in double quotes (SSIDs and URLs can have spaces)
    String quoted(String value) {
      value.replace("\"", "'");
      return "\"" + value + "\"";
    }

    int bitsPerPixel() {
      switch (bbep.getMode()) {
      case BB_MODE_1BPP:
        return 1;
      case BB_MODE_2BPP:
        return 2;
      case BB_MODE_4BPP:
        return 4;
      default:
        return 0;
      }
    }

    void printHelp() {
      Serial.println("REMOTE:HELP CMD:PING CMD:HOLD CMD:STATUS CMD:SHOT CMD:REFRESH CMD:WAKE:TIMER "
                     "CMD:BUTTON:SHORT CMD:BUTTON:DOUBLE CMD:BUTTON:LONG CMD:RESTART CMD:SERVER[:URL|:-] "
                     "CMD:FREE CMD:SLEEP");
    }

    void printPong() {
      const State &s = state();
      Serial.printf("REMOTE:PONG version=%s fw=%s seq=%u hold=%u\n", paperdiy_ota::version().c_str(),
                    Messages::firmware_version().c_str(), (unsigned)s.seq, (unsigned)s.hold);
    }

    void printStatus() {
      const float volts = paperdiy::batteryVolts();
      const bool wifi = WiFi.status() == WL_CONNECTED;
      String line = "REMOTE:STATUS";
      line += " version=" + paperdiy_ota::version();
      line += " wake=" + String((int)wakeup_reason);
      line += " pm1=" + String(paperdiy::begin() ? 1 : 0);
      line += " battery_mv=" + String(volts > 0 ? (int)(volts * 1000.0f + 0.5f) : -1);
      line += " wake_battery_mv=" + String((int)(battery_voltage * 1000.0f + 0.5f));
      line += " battery_pct=" + String(paperdiy_battery::reportPercent(battery_voltage));
      line += " usb=" + String(paperdiy::usbStatus() == UsbStatus::CONNECTED ? 1 : 0);
      line += " charging=" + String(paperdiy::chargingStatus() == ChargingStatus::CHARGING ? 1 : 0);
      line += " launcher=" + String(paperdiy::underLauncher() ? 1 : 0);
      // The wake turns Wi-Fi off once the image is in (display_session.cpp), so
      // wifi is usually 0 here; wifi_saved says whether it has networks to join
      line += " wifi_saved=" + String(WifiCaptivePortal.isSaved() ? 1 : 0);
      line += " wifi=" + String(wifi ? 1 : 0);
      line += " ssid=" + quoted(wifi ? WiFi.SSID() : String(""));
      line += " rssi=" + String(wifi ? (int)WiFi.RSSI() : 0);
      line += " ip=" + (wifi ? WiFi.localIP().toString() : String(""));
      line += " api_url=" + quoted(preferences.getString(PREFERENCES_API_URL, ""));
      line += " api_key=" + String(preferences.getString(PREFERENCES_API_KEY, "").length() > 0 ? 1 : 0);
      line += " friendly_id=" + quoted(preferences.getString(PREFERENCES_FRIENDLY_ID, ""));
      line += " refresh_s=" + String((unsigned)refreshInterval.seconds());
      line += " panel=" + String(bbep.width()) + "x" + String(bbep.height());
      line += " bpp=" + String(bitsPerPixel());
      line += " rotation=" + String(bbep.getRotation());
      line += " heap=" + String(ESP.getFreeHeap());
      line += " psram=" + String(ESP.getFreePsram());
      line += " uptime_ms=" + String(millis());
      line += " seq=" + String((unsigned)state().seq);
      Serial.println(line);
    }

    // The framebuffer as the last refresh left it (display_sleep() only stops the
    // bus): "#shot W H BPP ROTATION BYTES", one "#r Y <hex>" a row, "#end <crc32>"
    void sendShot() {
      const uint8_t *buffer = bbep.currentBuffer();
      const int bpp = bitsPerPixel();
      const int width = bbep.width();
      const int height = bbep.height();
      const size_t pitch = ((size_t)width * bpp + 7) / 8;
      if (buffer == nullptr || bpp == 0 || pitch == 0 || pitch > MAX_ROW_BYTES || bbep.getRotation() != 0) {
        Serial.printf("REMOTE:ERR SHOT no framebuffer (mode %d, %dx%d, rotation %d)\n", bbep.getMode(), width, height,
                      bbep.getRotation());
        return;
      }
      static const char HEX_DIGITS[] = "0123456789abcdef";
      static char row[16 + 2 * MAX_ROW_BYTES + 2];
      Serial.printf("#shot %d %d %d %d %u\n", width, height, bpp, bbep.getRotation(), (unsigned)(pitch * height));
      uint32_t crc = 0;
      for (int y = 0; y < height; y++) {
        const uint8_t *src = buffer + (size_t)y * pitch;
        int n = snprintf(row, sizeof(row), "#r %d ", y);
        for (size_t x = 0; x < pitch; x++) {
          row[n++] = HEX_DIGITS[src[x] >> 4];
          row[n++] = HEX_DIGITS[src[x] & 0x0f];
        }
        row[n++] = '\n';
        // One write a row, so log lines from other tasks land between rows, not inside one
        Serial.write((const uint8_t *)row, n);
        crc = crc32Update(crc, src, pitch);
      }
      Serial.printf("#end %08lx\n", (unsigned long)crc);
    }

    void handleServer(const char *arg) {
      const String current = preferences.getString(PREFERENCES_API_URL, "");
      if (arg[0] == '\0') {
        Serial.println("REMOTE:SERVER url=" + quoted(current) + " default=" + quoted(API_BASE_URL));
        return;
      }
      // Unlike the portal, this keeps the API key and friendly ID, so the board
      // comes back to its own server unchanged once the URL is restored
      if (strcmp(arg, "-") == 0) {
        preferences.remove(PREFERENCES_API_URL);
      } else {
        const String url(arg);
        if (!(url.startsWith("http://") || url.startsWith("https://")) || url.length() >= 128) {
          Serial.println("REMOTE:ERR SERVER needs an http:// or https:// URL under 128 characters");
          return;
        }
        preferences.putString(PREFERENCES_API_URL, url);
      }
      Serial.println("REMOTE:OK SERVER old=" + quoted(current) + " new=" +
                     quoted(preferences.getString(PREFERENCES_API_URL, "")));
    }

    [[noreturn]] void restartAs(Wake wake, int press, Command command) {
      State &s = state();
      s.wake = (uint8_t)wake;
      s.press = (uint8_t)press;
      Serial.printf("REMOTE:OK %s restarting hold=%u\n", paperdiy_remote::name(command), (unsigned)s.hold);
      Serial.flush();
      delay(200);
      ESP.restart();
      while (true) {
      }
    }

    // Runs one command; false leaves the loop so the board sleeps
    bool handle(const char *received) {
      const char *line = paperdiy_remote::commandStart(received);
      const paperdiy_remote::Parsed parsed = paperdiy_remote::parse(line);
      State &s = state();
      switch (parsed.command) {
      case Command::None:
        return true;
      case Command::Unknown:
        Serial.printf("REMOTE:ERR unknown %s\n", line);
        printHelp();
        return true;
      case Command::Ping:
        printPong();
        return true;
      case Command::Hold:
        s.hold = 1;
        Serial.println("REMOTE:OK HOLD");
        return true;
      case Command::Status:
        printStatus();
        return true;
      case Command::Shot:
        sendShot();
        return true;
      case Command::Refresh:
      case Command::ButtonShort:
        restartAs(Wake::Button, ShortPress + 1, parsed.command);
      case Command::ButtonDouble:
        restartAs(Wake::Button, DoubleClick + 1, parsed.command);
      case Command::ButtonLong:
        restartAs(Wake::Button, LongPress + 1, parsed.command);
      case Command::WakeTimer:
        restartAs(Wake::Timer, 0, parsed.command);
      case Command::Restart:
        restartAs(Wake::None, 0, parsed.command);
      case Command::Server:
        handleServer(parsed.arg);
        return true;
      case Command::Free:
        s.hold = 0;
        Serial.println("REMOTE:OK FREE");
        return true;
      case Command::Sleep:
        s.hold = 0;
        Serial.println("REMOTE:OK SLEEP");
        Serial.flush();
        return false;
      case Command::Help:
        printHelp();
        return true;
      }
      return true;
    }

  } // namespace

  bool injectedWake(esp_sleep_wakeup_cause_t *reason) {
    State &s = state();
    const Wake wake = (Wake)s.wake;
    s.wake = (uint8_t)Wake::None;
    if (wake == Wake::Timer) {
      *reason = ESP_SLEEP_WAKEUP_TIMER;
    } else if (wake == Wake::Button) {
      *reason = ESP_SLEEP_WAKEUP_EXT0;
    } else {
      s.press = 0; // a press only comes with a button wake
      return false;
    }
    Log_info("PaperDIY dev remote: simulating a %s wake", wake == Wake::Timer ? "timer" : "button");
    return true;
  }

  ButtonPressResult injectedPressOr(ButtonPressResult (*read_buttons)()) {
    State &s = state();
    const uint8_t press = s.press;
    s.press = 0;
    if (press == 0 || press > NoAction + 1) return read_buttons();
    const ButtonPressResult result = (ButtonPressResult)(press - 1);
    Log_info("PaperDIY dev remote: simulating a %s", ButtonPressResultNames[result]);
    return result;
  }

  void beforeSleep() {
    State &s = state();
    if (paperdiy::onBattery()) { // the board powers off: nothing could talk to it
      s.hold = 0;
      return;
    }
    Serial.begin(115200); // does nothing if serial logging already started it

    LineReader reader;
    bool have_line = false;
    if (!s.hold) {
      // Commands sent during the wake are waiting; a host can also catch the board here
      const uint32_t start = millis();
      while (millis() - start < LISTEN_MS) {
        if (reader.poll() &&
            paperdiy_remote::parse(paperdiy_remote::commandStart(reader.line())).command != Command::None) {
          have_line = true;
          break;
        }
        delay(5);
      }
      if (!have_line) return;
    }

    s.hold = 1;
    s.seq++;
    awake_time_limit_stop(); // the loop has its own idle limit
    paperdiy_ota::confirmRunningImage(); // reached the end of a wake: a new image is good
    Log_info("PaperDIY dev remote: holding the board awake (session %u)", (unsigned)s.seq);
    Serial.printf("REMOTE:READY seq=%u version=%s wake=%d\n", (unsigned)s.seq, paperdiy_ota::version().c_str(),
                  (int)wakeup_reason);

    uint32_t last_command_ms = millis();
    uint32_t last_check_ms = millis();
    while (true) {
      if (have_line || reader.poll()) {
        have_line = false;
        last_command_ms = millis();
        if (!handle(reader.line())) break;
      }
      if (millis() - last_command_ms >= IDLE_MS) {
        Serial.println("REMOTE:IDLE no command for 15 minutes, sleeping");
        s.hold = 0;
        break;
      }
      if (millis() - last_check_ms >= BATTERY_CHECK_MS) {
        last_check_ms = millis();
        if (paperdiy::onBattery()) {
          Serial.println("REMOTE:BATTERY USB is gone, sleeping");
          s.hold = 0;
          break;
        }
      }
      delay(5);
    }
    Log_info("PaperDIY dev remote: released, going to sleep");
  }

} // namespace paperdiy_dev_remote

#endif // BOARD_TRMNL_X_PAPERDIY && PAPERDIY_DEV_REMOTE
