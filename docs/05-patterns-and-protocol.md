# 5. Wire Protocol, Custom Patterns & SDK Scripting

## Wire Protocol
Both **TCP (`7777`)** and **Bluetooth RFCOMM Channel 2** share the same dual-framing stream protocol (plus **UDP (`7778`)** for optional low-latency frames when on PAN):

1. **JSON-line commands** (leading byte `{` / `0x7B`, terminated by `\n`):
   - `status` / `hello` -> returns power, brightness, busy, active_mode, zones, total_leds, wall_epoch, pairing.
   - `sync_time` (`{"cmd":"sync_time","epoch_ms":1794624000000}`) -> updates Pi software clock offset for offline `seasonal(theme="auto")`.
   - `set_mode` (`{"cmd":"set_mode","name":"wave","params":{"speed":0.2}}`)
   - `set_busy` (`{"cmd":"set_busy","enabled":true}`)
   - `set_power` (`{"cmd":"set_power","on":false}`)
   - `set_brightness` (`{"cmd":"set_brightness","level":0.5}`)
   - `set_leds` (`{"cmd":"set_leds","updates":[{"zone":"strip_1","color":"#FF0000"}],"clear_rest":false}`)
   - `upload_pattern` (`{"cmd":"upload_pattern","pattern":{...},"activate":true}`)
   - `list_patterns`, `delete_pattern`, `get_config`, `set_segment_map`, `set_color_order`, `calibrate`, `enter_pairing`.

2. **Compact Binary Frame Packet** (leading byte `0x01`):
   ```text
   Byte 0:      0x01 (magic)
   Byte 1:      flags (0x00 = transient live stream; 0x01 = persist as static frame)
   Byte 2:      brightness (0..255)
   Bytes 3..4:  payload_len (uint16 big-endian, 29 * 3 = 87)
   Bytes 5..N:  RGB triplets [R0, G0, B0, R1, G1, B1, ...]
   ```
   Total packet size for 29 LEDs is **92 bytes**. If no live frame arrives for `stream_timeout_s` (default `5.0s`), the Pi gracefully reverts to the underlying Busy or autonomous mode.

---

## Built-in Shaders & Parameters
- `rainbow`: `speed` (`0.05`), `scale` (`1.0`), `saturation` (`1.0`), `axis` (`"u" | "x" | "y" | "2d"`), `reverse` (`false`)
- `pulse`: `color` (`"#FF1800"`), `speed` (`0.35`), `min_level` (`0.08`), `max_level` (`1.0`)
- `wave`: `colors` (`["#002B7F", "#0088FF", "#00E5FF"]`), `speed` (`0.18`), `scale` (`1.4`), `axis` (`"u"`)
- `seasonal`: `theme` (`"auto" | "spring" | "summer" | "fall" | "halloween" | "winter" | "holiday" | "pride"`), `speed` (`0.08`), `scale` (`1.0`)
- `warm_desk`: `warmth` (`0.85`), `speed` (`0.04`)
- `sparkle`: `base` (`"#0A1833"`), `spark` (`"#88CCFF"`), `speed` (`1.1`), `density` (`0.2`)
- `chase`: `color` (`"#00FFAA"`), `bg` (`"#040812"`), `speed` (`0.35`), `tail` (`0.25`)
- `gradient`: `colors` (`["#FF4E50", "#FC913A", "#F9D423"]`), `speed` (`0.0`), `axis` (`"u"`)
- `solid`: `color` (`"#4488FF"`), optional `zones` (`{"strip_1": "#FF0000", "strip_2": "#00FF00"}`)

---

## Writing Declarative Keyframe Patterns (`.json`)
Declarative patterns run completely autonomously on the Pi after upload (see [patterns/aurora_cascade.json](file:///c:/Users/DiddyWolf/Projects/cubelights/patterns/aurora_cascade.json)):

```json
{
  "name": "my_custom_fade",
  "description": "Smooth transition across named zones and specific LEDs",
  "loop": 0,
  "keyframes": [
    {
      "duration_ms": 1500,
      "easing": "ease_in_out",
      "fill": "#050A18",
      "targets": [
        {"zone": "strip_1", "color": "#00AAFF"},
        {"start": 6, "end": 18, "color": "#0044AA"}
      ]
    },
    {
      "duration_ms": 1500,
      "easing": "ease_in_out",
      "fill": "#050A18",
      "targets": [
        {"indices": [0, 1, 2], "color": "#FF8800"},
        {"zone": "strip_3", "color": "#FF2266"}
      ]
    }
  ]
}
```

Upload and run via CLI:
```powershell
cubelights upload my_custom_fade.json
```

---

## Writing Custom Python Shaders (`/data/shaders/*.py`)
Drop any `.py` file into `/data/shaders/` (see [docs/04-maintenance-and-backups.md](file:///c:/Users/DiddyWolf/Projects/cubelights/docs/04-maintenance-and-backups.md) for remounting `/data` `rw`) and restart `cubelights`:

```python
# /data/shaders/matrix_rain.py
import math

NAME = "matrix_rain"

def render(t: float, seg_map, params: dict) -> list[tuple[int, int, int]]:
    speed = float(params.get("speed", 0.5))
    pixels = []
    for pt in seg_map.points:
        glow = max(0.0, math.sin(2.0 * math.pi * (pt.y * 1.5 - t * speed + pt.x * 0.3)))
        pixels.append((0, int(40 + 215 * (glow ** 3)), int(20 * glow)))
    return pixels
```

---

## Python SDK Live Streaming Example (From Windows 11)
Stream custom 45 FPS per-LED animations directly from your work laptop over Bluetooth:

```python
import math
import time
from cubelights_client import CubeLightsClient

with CubeLightsClient.connect() as cl:
    total = cl.status()["total_leds"]
    with cl.stream(fps=45.0, brightness=0.8) as stream:
        start = time.monotonic()
        while time.monotonic() - start < 15.0:
            t = time.monotonic() - start
            frame = [
                (
                    int(127 + 127 * math.sin(t * 3.0 + i * 0.35)),
                    int(127 + 127 * math.sin(t * 3.0 + i * 0.35 + 2.1)),
                    int(127 + 127 * math.sin(t * 3.0 + i * 0.35 + 4.2)),
                )
                for i in range(total)
            ]
            stream.push(frame)
```
