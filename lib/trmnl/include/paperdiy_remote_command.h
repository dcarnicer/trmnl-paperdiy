#pragma once

// Command lines of the PaperDIY dev remote (src/paperdiy_dev_remote.cpp), kept
// free of Arduino headers so the native tests can cover them.
//
// One command a line over the USB serial port: "CMD:<NAME>[:<ARG>]", case
// sensitive, as in the PaperDIY CrossPoint port's "CMD:KEY:<key>". Anything else
// on the line is log noise or a partial line and is ignored.

#include <stddef.h>

namespace paperdiy_remote {

  enum class Command {
    None,    // not a command line
    Unknown, // "CMD:" with a name this firmware does not know
    Ping,
    Hold,
    Status,
    Shot,
    Refresh, // the same as ButtonShort
    WakeTimer,
    ButtonShort,
    ButtonDouble,
    ButtonLong,
    Restart,
    Server,
    Free,
    Sleep,
    Help,
  };

  struct Parsed {
    Command command;
    // SERVER's value, pointing into the line: "" shows the server, "-" restores
    // the default, anything else is the new URL. "" for the other commands.
    const char *arg;
  };

  /// @brief Strips trailing CR, LF, spaces and tabs in place.
  /// @return the new length
  size_t trimLine(char *line);

  /// @brief Where the command on @p line starts: its last "CMD:". The host's
  ///        CMD:HOLD lines overflow the board's 256-byte USB buffer while it is
  ///        busy, and the bytes lost can glue half a command to the next one
  ///        ("CMD:CMD:PING", "LDCMD:PING"); the last one arrived whole. So a
  ///        SERVER URL cannot contain "CMD:". Returns @p line when it has none.
  const char *commandStart(const char *line);

  /// @brief Parses one trimmed line (pass it through commandStart() first).
  Parsed parse(const char *line);

  /// @brief The command's name as written after "CMD:" ("BUTTON:SHORT"), or
  ///        "NONE" / "UNKNOWN".
  const char *name(Command command);

} // namespace paperdiy_remote
