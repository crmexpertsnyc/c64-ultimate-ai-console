/*
 * C64 Joystick Bridge — ESP32 firmware for the C64 Ultimate AI Console.
 *
 * Closes the five joystick switches (up, down, left, right, fire) of a Commodore 64 control
 * port on command from the console over Wi-Fi (UDP). Wiring and build guide:
 * docs/joystick-bridge.md. Protocol: backend/app/ultimate/joybridge.py.
 *
 * Board:   any ESP32 dev board (ESP32-WROOM-32 "DevKitC" / "DOIT ESP32 DEVKIT V1").
 * Arduino: Boards Manager → "esp32 by Espressif Systems" (2.x or 3.x). No extra libraries.
 *
 * Also understands the UniJoystiCle v1 UDP packet (4 bytes: version 2, mode, joy1, joy2 — same
 * port and bit layout), so existing UniJoystiCle phone/desktop apps can drive this bridge too.
 *
 * Safety:
 *   - Never connect a GPIO straight to the joystick port: drive an optocoupler (or transistor)
 *     that pulls the C64 line to the port's GND (pin 8). See the wiring guide.
 *   - Every line is released at boot, when Wi-Fi drops, on RELEASE_ALL, and when no update has
 *     arrived for FAILSAFE_MS while anything is held (the console repeats state every 100 ms).
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ESPmDNS.h>
#include "config.h"

#define FW_MAJOR 1
#define FW_MINOR 0

static const uint8_t MAGIC[4] = {'C', '6', '4', 'J'};
static const uint8_t PROTO_VERSION = 1;
enum : uint8_t { T_STATE = 0x01, T_PING = 0x02, T_PONG = 0x03, T_RELEASE = 0x04 };

// Bit order matches the protocol: 0 up, 1 down, 2 left, 3 right, 4 fire.
static const int PINS[3][5] = {
  {-1, -1, -1, -1, -1},
  {PORT1_UP, PORT1_DOWN, PORT1_LEFT, PORT1_RIGHT, PORT1_FIRE},
  {PORT2_UP, PORT2_DOWN, PORT2_LEFT, PORT2_RIGHT, PORT2_FIRE},
};

WiFiUDP udp;
uint8_t masks[3] = {0, 0, 0};
uint32_t lastStateMs = 0;
uint16_t lastSeq = 0;
bool haveSeq = false;
bool wifiWasUp = false;
bool uniClient = false;  // last state came from a UniJoystiCle app (sends only on change)

static bool portWired(int port) {
  for (int b = 0; b < 5; b++) if (PINS[port][b] < 0) return false;
  return true;
}

static void writeLine(int pin, bool pressed) {
  if (pin < 0) return;
  // ACTIVE_HIGH: GPIO high lights the optocoupler LED / turns the NPN on = line pulled to GND.
  digitalWrite(pin, (pressed == (ACTIVE_HIGH != 0)) ? HIGH : LOW);
}

static void applyMask(int port, uint8_t mask) {
  if (port < 1 || port > 2 || !portWired(port)) return;
  mask &= 0x1F;
  if (mask == masks[port]) return;
  masks[port] = mask;
  for (int b = 0; b < 5; b++) writeLine(PINS[port][b], mask & (1 << b));
#if STATUS_LED >= 0
  digitalWrite(STATUS_LED, (masks[1] | masks[2]) ? HIGH : LOW);
#endif
#if DEBUG_SERIAL
  Serial.printf("port %d: %s%s%s%s%s%s\n", port, mask ? "" : "(released)",
                mask & 1 ? "up " : "", mask & 2 ? "down " : "", mask & 4 ? "left " : "",
                mask & 8 ? "right " : "", mask & 16 ? "fire" : "");
#endif
}

static void releaseAll() {
  applyMask(1, 0);
  applyMask(2, 0);
}

static void sendPong(uint16_t seq) {
  uint8_t buf[64];
  size_t n = 0;
  memcpy(buf, MAGIC, 4); n = 4;
  buf[n++] = PROTO_VERSION;
  buf[n++] = T_PONG;
  buf[n++] = seq & 0xFF;
  buf[n++] = seq >> 8;
  buf[n++] = (portWired(1) ? 1 : 0) | (portWired(2) ? 2 : 0);
  buf[n++] = masks[1];
  buf[n++] = masks[2];
  buf[n++] = FW_MAJOR;
  buf[n++] = FW_MINOR;
  buf[n++] = (uint8_t)(int8_t)WiFi.RSSI();
  uint32_t up = millis() / 1000;
  memcpy(buf + n, &up, 4); n += 4;  // ESP32 is little-endian, as the protocol requires
  const char *name = BRIDGE_NAME;
  size_t len = strlen(name);
  if (len > sizeof(buf) - n) len = sizeof(buf) - n;
  memcpy(buf + n, name, len); n += len;
  udp.beginPacket(udp.remoteIP(), udp.remotePort());
  udp.write(buf, n);
  udp.endPacket();
}

static void handlePacket() {
  uint8_t buf[64];
  int len = udp.read(buf, sizeof(buf));
  if (len == 4 && buf[0] == 2) {  // UniJoystiCle v1: [2][mode 1|2|3][joy1][joy2]
    uniClient = true;
    lastStateMs = millis();
    if (buf[1] & 1) applyMask(1, buf[2]);
    if (buf[1] & 2) applyMask(2, buf[3]);
    return;
  }
  if (len < 8 || memcmp(buf, MAGIC, 4) != 0 || buf[4] != PROTO_VERSION) return;
  uint8_t type = buf[5];
  uint16_t seq = buf[6] | (buf[7] << 8);
  switch (type) {
    case T_PING:
      sendPong(seq);
      break;
    case T_RELEASE:
      releaseAll();
      break;
    case T_STATE: {
      // UDP can reorder: ignore a packet older than the last one, unless the console went
      // quiet for a second (it may have restarted and reset its counter).
      uint32_t now = millis();
      if (haveSeq && now - lastStateMs < 1000 && (uint16_t)(seq - lastSeq) >= 0x8000) return;
      haveSeq = true;
      uniClient = false;
      lastSeq = seq;
      lastStateMs = now;
      for (int i = 8; i + 1 < len; i += 2) applyMask(buf[i], buf[i + 1]);
      break;
    }
  }
}

void setup() {
#if DEBUG_SERIAL
  Serial.begin(115200);
#endif
  for (int port = 1; port <= 2; port++)
    for (int b = 0; b < 5; b++)
      if (PINS[port][b] >= 0) {
        pinMode(PINS[port][b], OUTPUT);
        writeLine(PINS[port][b], false);
      }
#if STATUS_LED >= 0
  pinMode(STATUS_LED, OUTPUT);
  digitalWrite(STATUS_LED, LOW);
#endif
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(BRIDGE_NAME);
  WiFi.setSleep(false);  // modem sleep adds 100+ ms of latency
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void loop() {
  bool up = WiFi.status() == WL_CONNECTED;
  if (up && !wifiWasUp) {
    udp.begin(UDP_PORT);
    MDNS.begin(BRIDGE_NAME);  // reachable as <BRIDGE_NAME>.local
#if DEBUG_SERIAL
    Serial.printf("Wi-Fi up: %s  (UDP %d, %s.local)\n", WiFi.localIP().toString().c_str(), UDP_PORT, BRIDGE_NAME);
#endif
  } else if (!up && wifiWasUp) {
    releaseAll();  // never leave a direction held while unreachable
    udp.stop();
  }
  wifiWasUp = up;

  if (up) {
    while (udp.parsePacket() > 0) handlePacket();
  }
  // UniJoystiCle apps only send changes, so they get the original's longer inactivity timeout.
  uint32_t limit = uniClient ? UNI_FAILSAFE_MS : FAILSAFE_MS;
  if ((masks[1] | masks[2]) && millis() - lastStateMs > limit) {
#if DEBUG_SERIAL
    Serial.println("failsafe: no updates, releasing");
#endif
    releaseAll();
  }
  delay(1);
}
