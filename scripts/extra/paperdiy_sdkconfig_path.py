Import("env")
from pathlib import Path

# pioarduino passes board_build.esp-idf.sdkconfig_path to the bootloader's CMake
# subproject as given. A relative path does not resolve there, so the bootloader
# was built from ESP-IDF's defaults and missed our options (app rollback on the
# PaperDIY). Make it absolute before the framework builder reads it.
board = env.BoardConfig()
path = board.get("build.esp-idf.sdkconfig_path", "")
if path and not Path(path).is_absolute():
    board.update("build.esp-idf.sdkconfig_path", str(Path(env.subst("$PROJECT_DIR")) / path))
