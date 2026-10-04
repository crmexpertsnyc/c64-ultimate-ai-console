# Joystick bridge: control games from your PC, phone or gamepad

The C64 Ultimate's current firmware (1.1.0) cannot receive joystick input over the network. The
**joystick bridge** gets around that with a small ESP32 board plugged into the C64's joystick
port. When the console says "fire", the board closes the fire switch, exactly like a real
joystick does. It works with every game and every firmware, because to the C64 it simply
*is* a joystick.

What you get once it is running:

* The on‑screen joystick on the **Display** and **Controller** pages.
* **PC keyboard as joystick** (tick "Arrow keys + Space").
* **Gamepad as joystick**: plug an Xbox, PlayStation or 8BitDo pad into the PC (or pair it with a
  phone or tablet) and play on the Display page.
* AI agents (MCP `c64_joystick`) and the "Take over" vision mode can press the joystick too.

Cost: roughly **$15–20** in parts, plus about an hour with a soldering iron or breadboard.

> **Honest status:** the console side and the protocol are tested with a software stand‑in for the
> board, including against your real C64 backend. The ESP32 sketch follows the same protocol, but it
> has not been compiled or run on a real board yet. The wiring is the standard, well‑known method
> for C64 joystick adapters. Follow the safety notes.

---

## 1. Parts

| Qty | Part | Notes |
|---|---|---|
| 1 | **ESP32 dev board** ("ESP32‑WROOM‑32 DevKitC" or "DOIT ESP32 DEVKIT V1") | Must be the original ESP32, which has 2.4 GHz Wi‑Fi. About $6–10. |
| 5 | **PC817 optocoupler** (DIP‑4) | One per joystick line. 10 for both ports. |
| 5 | **330 Ω resistor** | One per optocoupler. 10 for both ports. |
| 1 | **DB9 *female* connector with screw terminals** (breakout board) | Plugs into the C64's joystick port. An old joystick extension cable, cut, also works. |
| 1 | **USB power supply** (5 V, 1 A) + USB cable | Powers the ESP32. |
| – | Breadboard or perfboard, jumper wires | |

Avoid ready‑made "optocoupler modules" unless their outputs are **open collector**. Many
include pull‑up resistors to their own supply, which you do not want on the C64's lines.

## 2. Safety (read before wiring)

* **Never connect an ESP32 pin directly to the joystick port.** The optocoupler is there so the
  ESP32 and the C64 are electrically separate. The board only ever *pulls a line to ground*, which
  is exactly what a joystick switch does, and it never drives a line high.
* **Do not power the ESP32 from the joystick port's +5 V (pin 7).** Wi‑Fi draws more current than
  that pin is meant to supply. Power the ESP32 from its own USB supply. With optocouplers, pin 7
  stays unconnected.
* **Never feed power in from two places at once** (for example USB plus the C64's pin 7).
* Wire and check everything with the C64 **switched off**. Plug the finished bridge in with the
  C64 off.

## 3. How it's wired

The C64 reads a joystick as five switches. Each switch connects a line to ground (pin 8).
The C64's control port pins:

| Pin | Signal | Pin | Signal |
|---|---|---|---|
| 1 | Up | 6 | **Fire** |
| 2 | Down | 7 | +5 V (leave unconnected) |
| 3 | Left | 8 | **GND** |
| 4 | Right | 9 | Paddle X (unused) |
| 5 | Paddle Y (unused) | | |

Pin numbers are mirrored depending on which side of a connector you look at. Use the
numbers **printed on your DB9 breakout** rather than counting pins yourself.

For **each** of the five lines, build this:

```
   ESP32                         PC817                        DB9 (to C64 port)
                             ┌───────────┐
 GPIO ──[ 330 Ω ]──────── 1 ─┤ Anode   Collector ├─ 4 ────────── line pin (1/2/3/4/6)
                             │    ▼  ↗         │
 GND  ─────────────────── 2 ─┤ Cathode  Emitter ├─ 3 ────────── pin 8 (GND)
                             └───────────┘
```

PC817 pins: **1** anode (dot), **2** cathode, **3** emitter, **4** collector.

| Line | C64 pin | ESP32 GPIO (port 2) | ESP32 GPIO (optional port 1) |
|---|---|---|---|
| Up | 1 | 18 | 25 |
| Down | 2 | 19 | 26 |
| Left | 3 | 21 | 27 |
| Right | 4 | 22 | 32 |
| Fire | 6 | 23 | 33 |

All five emitters (pin 3 of each PC817) go to DB9 **pin 8**. All five cathodes (pin 2) go to
an ESP32 **GND** pin.

**Port 2 is the one almost every game uses: start with that.** Port 1's lines are shared with
the keyboard, so games that read the keyboard can see phantom key presses on port 1. If you
only wire port 2, set the port‑1 pins to `-1` in `config.h`.

The GPIOs above avoid the ESP32's boot and flash pins, so the board always starts with every line
released. You can change them in `config.h`.

**Optional: keep your real joystick too.** A DB9 "Y" splitter lets the bridge and a real joystick
share port 2. Both only ever pull lines to ground, so they don't fight.

## 4. Put the firmware on the ESP32

1. Install the **Arduino IDE 2**. In **Boards Manager**, install **"esp32 by Espressif Systems"**.
2. Open `hardware/joybridge/joybridge.ino` from this project.
3. Copy `config.example.h` to **`config.h`** in the same folder. Enter your Wi‑Fi name and password.
   It must be a **2.4 GHz** network.
4. Plug the ESP32 into the PC. Choose **Tools → Board → ESP32 Dev Module** and the right **Port**,
   then **Upload**. Some boards need you to hold the **BOOT** button while it connects.
5. Open **Tools → Serial Monitor** at **115200** baud. You should see
   `Wi-Fi up: 192.168.1.xx (UDP 6464, c64-joybridge.local)`.

Tip: in your router, give the board a fixed address (a "DHCP reservation") so it never changes.

## 5. Connect it to the console

1. **Settings → Joystick bridge → 🔍 Find on network**, then click the board. Or type its address
   and click **Save**.
2. The top bar shows **🕹 joystick bridge**. Settings shows the round‑trip time and Wi‑Fi signal.
3. Click **Test wiring**. The console taps up, down, left, right and fire in turn. The ESP32's LED
   blinks and the Serial Monitor prints each press.

**Check it on the C64.** At the `READY.` prompt, type this with the **Type text** box on the Controller page, or
on the real keyboard:

```
10 PRINT PEEK(56320) AND 31 : GOTO 10
RUN
```

It prints **31** with nothing pressed. Up gives 30, down 29, left 27, right 23 and fire 15.
Combinations subtract together: up + fire gives 14. For port 1 use `PEEK(56321)`. Press
**RUN/STOP** on the real keyboard to stop the program.

Then load a game that says "press fire to start" and press **FIRE** on the Display page.

## 6. How it works (and why it's safe)

* **Protocol:** UDP port 6464. Each packet carries the full state of the port ("up + fire held"), not
  individual key events, so a lost packet can't leave something stuck. Details are in
  `backend/app/ultimate/joybridge.py`.
* **Failsafe:** while anything is held, the console repeats the state every 100 ms. If the board
  hears nothing for **350 ms**, it releases every line. That covers a closed browser tab, a PC that
  goes to sleep, or a Wi‑Fi drop. It also releases everything when it loses Wi‑Fi and when it boots.
  **RELEASE ALL** in the console releases the bridge too.
* **Latency:** a Wi‑Fi packet on a home network typically takes a few milliseconds. Power saving is
  switched off on the ESP32 so it answers immediately. The gamepad and keyboard use a WebSocket, not
  one web request per press.
* **UniJoystiCle compatible:** the board also accepts the 4‑byte UniJoystiCle v1 packet (same port,
  same bit layout), so the old UniJoystiCle phone and desktop controller apps can drive it too.
* **Only five lines:** one fire button. FIRE 2 and FIRE 3 (paddle lines) and the keyboard are not
  part of the bridge.

## 7. Try it without the board

The backend includes a software stand‑in that speaks the same protocol and prints every press:

```bash
cd backend
.venv\Scripts\python -m app.ultimate.joybridge_fake
```

In Settings, set the bridge address to `127.0.0.1`. Everything in the console behaves as if the
board were plugged in. Nothing reaches the C64, of course.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| "Find on network" finds nothing | Is the Serial Monitor showing "Wi‑Fi up"? It must be a 2.4 GHz network. Guest networks usually block device‑to‑device traffic. Try typing the IP from the Serial Monitor. |
| Top bar shows "bridge offline" | Board unpowered or off Wi‑Fi. Check the Serial Monitor. The address may have changed: use a DHCP reservation or `c64-joybridge.local`. |
| Serial Monitor shows presses but the C64 doesn't react | Wiring. Check the pin numbers on the DB9 breakout. Check that every emitter goes to pin 8. Check the PC817 orientation (the dot is pin 1). |
| A direction is always pressed | A collector or emitter is swapped or shorted to pin 8, or an optocoupler is in backwards. |
| Works on port 2, not in a particular game | That game reads port 1: switch ports on the joystick panel (wire port 1 too). |
| Weak Wi‑Fi (below −75 dBm in Settings) | Move the board or router closer. An ESP32 with an external antenna connector helps. |

---

## Appendix: other wireless options we looked at

| Option | PC‑controllable? | Notes |
|---|---|---|
| **This ESP32 bridge** | ✅ Yes (UDP) | Works today on firmware 1.1.0 and is the recommended route. |
| Future C64U firmware with the REST `machine:input` API | ✅ Yes, no hardware | Gideon's 3.15 firmware has it for Ultimate 64 boards only. Not in C64U 1.1.0. The console switches over automatically if it ever appears. |
| UniJoystiCle v1 (ESP8266, Wi‑Fi) | ✅ Yes | Open source, but no longer sold. Our bridge speaks its protocol. |
| UniJoystiCle 2 / BlueRetro (ESP32, Bluetooth gamepads) | ❌ Not from a PC | Great for playing with a Bluetooth pad on the real C64. They have no network input. |
| Wireless joystick kits (for example Atari CX40+ Wireless) | ❌ No | Paired controller and receiver only. |
| USB gamepads in the C64U's USB ports | ❌ No | The C64U's USB supports keyboards and mice, not gamepads. USB‑to‑DB9 adapters (MouSTer, USBtoC64) plus a Wi‑Fi‑driven USB gamepad would work, but that's two devices instead of one. |
