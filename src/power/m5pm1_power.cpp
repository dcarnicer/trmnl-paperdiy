#include <power.h>

#ifdef INCLUDE_M5PM1_POWER

#include <paperdiy_board.h>

UsbStatus M5PM1Power::usbStatus() { return paperdiy::usbStatus(); }

ChargingStatus M5PM1Power::chargingStatus() { return paperdiy::chargingStatus(); }

#endif // INCLUDE_M5PM1_POWER
