#include "paperdiy_remote_command.h"

#include <string.h>

namespace paperdiy_remote {

  namespace {

    const char PREFIX[] = "CMD:";
    const size_t PREFIX_LEN = sizeof(PREFIX) - 1;
    const char SERVER[] = "SERVER";
    const size_t SERVER_LEN = sizeof(SERVER) - 1;

    struct Entry {
      const char *name;
      Command command;
    };

    // Exact names; SERVER takes an argument and is parsed apart
    const Entry COMMANDS[] = {
      {"PING", Command::Ping},
      {"HOLD", Command::Hold},
      {"STATUS", Command::Status},
      {"SHOT", Command::Shot},
      {"REFRESH", Command::Refresh},
      {"WAKE:TIMER", Command::WakeTimer},
      {"BUTTON:SHORT", Command::ButtonShort},
      {"BUTTON:DOUBLE", Command::ButtonDouble},
      {"BUTTON:LONG", Command::ButtonLong},
      {"RESTART", Command::Restart},
      {"FREE", Command::Free},
      {"SLEEP", Command::Sleep},
      {"HELP", Command::Help},
    };

  } // namespace

  size_t trimLine(char *line) {
    size_t len = strlen(line);
    while (len > 0) {
      const char c = line[len - 1];
      if (c != '\r' && c != '\n' && c != ' ' && c != '\t') break;
      line[--len] = '\0';
    }
    return len;
  }

  const char *commandStart(const char *line) {
    if (line == NULL) return line;
    const char *start = strstr(line, PREFIX);
    if (start == NULL) return line;
    for (const char *next = strstr(start + 1, PREFIX); next != NULL; next = strstr(next + 1, PREFIX)) {
      start = next;
    }
    return start;
  }

  Parsed parse(const char *line) {
    if (line == NULL || strncmp(line, PREFIX, PREFIX_LEN) != 0) return Parsed{Command::None, ""};
    const char *rest = line + PREFIX_LEN;

    if (strncmp(rest, SERVER, SERVER_LEN) == 0) {
      const char next = rest[SERVER_LEN];
      if (next == '\0') return Parsed{Command::Server, ""};
      // The URL itself has colons ("http://host:8080"): only the first one separates it
      if (next == ':') return Parsed{Command::Server, rest + SERVER_LEN + 1};
    }

    for (size_t i = 0; i < sizeof(COMMANDS) / sizeof(COMMANDS[0]); i++) {
      if (strcmp(rest, COMMANDS[i].name) == 0) return Parsed{COMMANDS[i].command, ""};
    }
    return Parsed{Command::Unknown, ""};
  }

  const char *name(Command command) {
    if (command == Command::None) return "NONE";
    if (command == Command::Unknown) return "UNKNOWN";
    if (command == Command::Server) return SERVER;
    for (size_t i = 0; i < sizeof(COMMANDS) / sizeof(COMMANDS[0]); i++) {
      if (COMMANDS[i].command == command) return COMMANDS[i].name;
    }
    return "UNKNOWN";
  }

} // namespace paperdiy_remote
