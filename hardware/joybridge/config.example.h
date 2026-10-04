// Copy this file to config.h (same folder) and fill in your Wi-Fi details.
// config.h is not part of the project's documentation, so your password stays on your PC.
#pragma once

#define WIFI_SSID     "your-wifi-name"
#define WIFI_PASSWORD "your-wifi-password"   // 2.4 GHz network: the ESP32 has no 5 GHz radio

#define BRIDGE_NAME   "c64-joybridge"        // shown in the console; also <name>.local
#define UDP_PORT      6464                   // must match JOYBRIDGE_PORT in the console

// Pins driving the optocouplers (ESP32 GPIO numbers). These avoid boot-strapping and
// flash pins on ESP32 DevKit boards. Set a whole port to -1 if you do not wire it.
// Port 2 is what almost every game uses — wire that one first.
#define PORT2_UP     18
#define PORT2_DOWN   19
#define PORT2_LEFT   21
#define PORT2_RIGHT  22
#define PORT2_FIRE   23

#define PORT1_UP     25   // optional second port: use -1 on all five to disable
#define PORT1_DOWN   26
#define PORT1_LEFT   27
#define PORT1_RIGHT  32
#define PORT1_FIRE   33

#define ACTIVE_HIGH  1    // 1 for optocouplers / NPN transistors as in the guide
#define FAILSAFE_MS  350  // release everything when the console goes quiet this long
#define UNI_FAILSAFE_MS 10000  // same, for UniJoystiCle apps (they only send changes)
#define STATUS_LED   2    // on-board LED lights while anything is pressed; -1 = off
#define DEBUG_SERIAL 1    // print presses on the USB serial monitor (115200 baud)
