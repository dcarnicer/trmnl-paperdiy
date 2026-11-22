#include <paperdiy_remote_command.h>
#include <string.h>
#include <unity.h>

using paperdiy_remote::Command;
using paperdiy_remote::commandStart;
using paperdiy_remote::name;
using paperdiy_remote::parse;
using paperdiy_remote::trimLine;

void test_plain_commands(void) {
  TEST_ASSERT_TRUE(parse("CMD:PING").command == Command::Ping);
  TEST_ASSERT_TRUE(parse("CMD:HOLD").command == Command::Hold);
  TEST_ASSERT_TRUE(parse("CMD:STATUS").command == Command::Status);
  TEST_ASSERT_TRUE(parse("CMD:SHOT").command == Command::Shot);
  TEST_ASSERT_TRUE(parse("CMD:REFRESH").command == Command::Refresh);
  TEST_ASSERT_TRUE(parse("CMD:WAKE:TIMER").command == Command::WakeTimer);
  TEST_ASSERT_TRUE(parse("CMD:BUTTON:SHORT").command == Command::ButtonShort);
  TEST_ASSERT_TRUE(parse("CMD:BUTTON:DOUBLE").command == Command::ButtonDouble);
  TEST_ASSERT_TRUE(parse("CMD:BUTTON:LONG").command == Command::ButtonLong);
  TEST_ASSERT_TRUE(parse("CMD:RESTART").command == Command::Restart);
  TEST_ASSERT_TRUE(parse("CMD:FREE").command == Command::Free);
  TEST_ASSERT_TRUE(parse("CMD:SLEEP").command == Command::Sleep);
  TEST_ASSERT_TRUE(parse("CMD:HELP").command == Command::Help);
  TEST_ASSERT_EQUAL_STRING("", parse("CMD:PING").arg);
}

void test_not_a_command(void) {
  TEST_ASSERT_TRUE(parse("").command == Command::None);
  TEST_ASSERT_TRUE(parse(NULL).command == Command::None);
  TEST_ASSERT_TRUE(parse("[  1234][I][bl.cpp:62] BL done").command == Command::None);
  TEST_ASSERT_TRUE(parse("MD:PING").command == Command::None); // the start of the line was lost
  TEST_ASSERT_TRUE(parse("cmd:ping").command == Command::None);
}

void test_unknown_commands(void) {
  TEST_ASSERT_TRUE(parse("CMD:").command == Command::Unknown);
  TEST_ASSERT_TRUE(parse("CMD:ping").command == Command::Unknown); // case sensitive
  TEST_ASSERT_TRUE(parse("CMD:PINGX").command == Command::Unknown);
  TEST_ASSERT_TRUE(parse("CMD:BUTTON").command == Command::Unknown);
  TEST_ASSERT_TRUE(parse("CMD:BUTTON:RESET").command == Command::Unknown); // the credential wipe is left out
  TEST_ASSERT_TRUE(parse("CMD:SERVERS").command == Command::Unknown);
  TEST_ASSERT_TRUE(parse("CMD:PING:1").command == Command::Unknown);
}

void test_server_argument(void) {
  paperdiy_remote::Parsed show = parse("CMD:SERVER");
  TEST_ASSERT_TRUE(show.command == Command::Server);
  TEST_ASSERT_EQUAL_STRING("", show.arg);

  paperdiy_remote::Parsed set = parse("CMD:SERVER:http://192.168.1.42:8080");
  TEST_ASSERT_TRUE(set.command == Command::Server);
  TEST_ASSERT_EQUAL_STRING("http://192.168.1.42:8080", set.arg);

  TEST_ASSERT_EQUAL_STRING("-", parse("CMD:SERVER:-").arg);
  TEST_ASSERT_EQUAL_STRING("", parse("CMD:SERVER:").arg);
}

void test_trim_line(void) {
  char line[32];
  strcpy(line, "CMD:PING\r\n");
  TEST_ASSERT_EQUAL(8, trimLine(line));
  TEST_ASSERT_EQUAL_STRING("CMD:PING", line);
  TEST_ASSERT_TRUE(parse(line).command == Command::Ping);

  strcpy(line, " \t\r");
  TEST_ASSERT_EQUAL(0, trimLine(line));
  TEST_ASSERT_EQUAL_STRING("", line);

  strcpy(line, " CMD:PING"); // leading spaces stay: not a command
  TEST_ASSERT_EQUAL(9, trimLine(line));
  TEST_ASSERT_TRUE(parse(line).command == Command::None);
}

void test_command_start_resyncs_after_lost_bytes(void) {
  // Seen on the board: the rest of a flooded CMD:HOLD was lost, its "CMD:" stayed
  TEST_ASSERT_EQUAL_STRING("CMD:PING", commandStart("CMD:CMD:PING"));
  TEST_ASSERT_TRUE(parse(commandStart("CMD:CMD:PING")).command == Command::Ping);
  TEST_ASSERT_TRUE(parse(commandStart("LDCMD:PING")).command == Command::Ping);
  TEST_ASSERT_TRUE(parse(commandStart("CMD:HOCMD:HOLD")).command == Command::Hold);
  TEST_ASSERT_EQUAL_STRING("CMD:SERVER:http://192.168.1.42:8086", commandStart("CMD:SERVER:http://192.168.1.42:8086"));
  TEST_ASSERT_EQUAL_STRING("no command here", commandStart("no command here"));
  TEST_ASSERT_TRUE(commandStart(NULL) == NULL);
}

void test_names(void) {
  TEST_ASSERT_EQUAL_STRING("BUTTON:SHORT", name(Command::ButtonShort));
  TEST_ASSERT_EQUAL_STRING("WAKE:TIMER", name(Command::WakeTimer));
  TEST_ASSERT_EQUAL_STRING("SERVER", name(Command::Server));
  TEST_ASSERT_EQUAL_STRING("NONE", name(Command::None));
  TEST_ASSERT_EQUAL_STRING("UNKNOWN", name(Command::Unknown));
}

int main(int argc, char **argv) {
  UNITY_BEGIN();
  RUN_TEST(test_plain_commands);
  RUN_TEST(test_not_a_command);
  RUN_TEST(test_unknown_commands);
  RUN_TEST(test_server_argument);
  RUN_TEST(test_trim_line);
  RUN_TEST(test_command_start_resyncs_after_lost_bytes);
  RUN_TEST(test_names);
  return UNITY_END();
}
