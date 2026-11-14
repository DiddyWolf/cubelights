# 1. Hardware Assembly & Pinout

## Components
- **Raspberry Pi Zero W v1.1** (ARMv6 BCM2835, 40-pin GPIO header)
- **Pimoroni Button SHIM** (5 tactile buttons `A`–`E` + 1 APA102 RGB status LED via TCA9554A I2C expander at `0x3F`)
- **Maniacal Labs PiPixel HAT** (5V logic level shifter `74AHCT125`, power input terminal/jack up to 5A)
- **LED Strips**: 3 APA102 / SK9822 4-wire strips wired in series (`6` + `13` + `10` = `29` LEDs total)
- *(Optional for initial setup/recovery)* **Adafruit PiUART** (USB-to-TTL serial adapter on UART TX/RX)

## GPIO Pin Allocation (Zero Conflicts)

| Peripheral | Interface | Pins Used (BCM) | Notes |
| --- | --- | --- | --- |
| **PiPixel LEDs** | SPI0 (`spidev0.0`) | **GPIO 10** (MOSI/Data), **GPIO 11** (SCLK/Clock) | Requires 2x3 jumper on **`SPI_DAT`** |
| **Button SHIM** | I2C1 (`i2c-1`, `0x3F`) | **GPIO 2** (SDA), **GPIO 3** (SCL) | Polls 5 buttons + drives onboard RGB LED |
| **Adafruit PiUART** | UART0 | **GPIO 14** (TXD), **GPIO 15** (RXD) | Optional debug console; unused in normal desk operation |

## PiPixel HAT Setup
1. Verify the 2x3 GPIO selection jumper on the PiPixel board is positioned on **`SPI_DAT`** (not `GP18` or `GP13`).
2. Connect the 4-wire harness from your first LED strip (`6` LEDs) to the PiPixel output header/screw terminals:
   - `5V` -> Red
   - `GND` -> Black
   - `DAT` (`DI`) -> Data wire (match strip markings `DI`/`CI`)
   - `CLK` (`CI`) -> Clock wire
3. Power the setup through the PiPixel barrel jack or green 2-pin screw terminal using a **regulated 5V DC power supply** (2A+ recommended).
   - *Power budget*: 29 APA102 LEDs at 100% full white draw ~1.74A (`29 × 60 mA`), plus ~0.2A for the Pi Zero W. The default software brightness cap (`max_brightness_cap = 0.75`) keeps peak draw comfortably under ~1.5A.

> [!WARNING]
> **Never connect >5V to a 5V APA102 strip!** PiPixel passes input voltage directly to the LED rail while regulating 5V for the Pi. Always confirm your power supply is **5V DC** before powering on.

## Physical Segment Orientation (`6 + 13 + 10`)
Because the 3 strips are connected in series with braided wires, one strip may be mounted left-to-right while another snakes right-to-left or vertically.
`cubelights` solves this in `/data/config.json` via `segments`:

```json
"segments": [
  {"name": "strip_1", "length": 6,  "reversed": false, "start_xy": [0.0, 0.25], "end_xy": [0.35, 0.25]},
  {"name": "strip_2", "length": 13, "reversed": false, "start_xy": [0.0, 0.65], "end_xy": [1.0,  0.65]},
  {"name": "strip_3", "length": 10, "reversed": false, "start_xy": [0.15, 1.0], "end_xy": [0.85, 1.0]}
]
```

After mounting the strips in your cubicle, run `cubelights calibrate` to see each segment light up in order (`strip_1` = Red, `strip_2` = Green, `strip_3` = Blue, ramping dim→bright along its logical start→end). Flip `"reversed": true` on any segment whose bright end is opposite your desired flow direction.
