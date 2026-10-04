// Preset for the ready-made Waveshare ESP32-S3-Relay-6CH (no breadboard, no chips).
// Copy this file to config.h (same folder) and fill in your Wi-Fi details.
//
// Arduino IDE: Tools → Board → esp32 → "ESP32S3 Dev Module", and Tools → USB CDC On Boot → Enabled
// (so the Serial Monitor works over the USB-C port).
//
// Wiring (C64 switched off), using each relay's COM and NO terminals:
//   CH1 NO → DB9 pin 1 (up)      CH4 NO → DB9 pin 4 (right)
//   CH2 NO → DB9 pin 2 (down)    CH5 NO → DB9 pin 6 (fire)
//   CH3 NO → DB9 pin 3 (left)    COM of CH1..CH5 (linked together) → DB9 pin 8 (GND)
// Leave NC terminals, CH6, and DB9 pins 5, 7, 9 unconnected. Power the board from USB-C only.
// Relay GPIOs from the Waveshare wiki (CH1..CH6 = GPIO 1, 2, 41, 42, 45, 46).
#pragma once

#define WIFI_SSID     "your-wifi-name"
#define WIFI_PASSWORD "your-wifi-password"   // 2.4 GHz network

#define BRIDGE_NAME   "c64-joybridge"
#define UDP_PORT      6464

#define PORT2_UP     1    // CH1
#define PORT2_DOWN   2    // CH2
#define PORT2_LEFT   41   // CH3
#define PORT2_RIGHT  42   // CH4
#define PORT2_FIRE   45   // CH5

#define PORT1_UP     -1   // only one port fits on 6 relays
#define PORT1_DOWN   -1
#define PORT1_LEFT   -1
#define PORT1_RIGHT  -1
#define PORT1_FIRE   -1

#define ACTIVE_HIGH  1    // unverified: if every relay is ON while idle, change to 0
#define FAILSAFE_MS  350
#define UNI_FAILSAFE_MS 10000
#define STATUS_LED   -1   // GPIO2 drives relay CH2 on this board, so no status LED
#define DEBUG_SERIAL 1
