# daytime SC20 for Home Assistant

Local control of the **daytime Smart Control SC20** aquarium LED controller (OEM:
aquaLEDs.de UG "Light-Symphony") from Home Assistant — lights, the daycycle schedule, the
moon and cloud effects, and diagnostics.

The SC20 has no published API. Everything here was recovered by reverse engineering the
device's own web app and the vendor firmware image, then confirmed against real hardware.
The full protocol reference, including what is proven and what is still a guess, is in
[`docs/protocol/`](docs/protocol/).

## What you get

| Entity | What it does |
|---|---|
| `light.<tank>` | Master light — on/off and brightness, scaling all three channels while keeping their colour ratio |
| `light.<tank>_white` / `_blue` / `_red` | The three channels individually |
| `select.<tank>_mode` | **Daycycle** (follow the stored programme) or **Manual** |
| `switch.<tank>_moonlight` | Moonlight simulation |
| `switch.<tank>_cloud_simulation` | Cloud simulation |
| `switch.<tank>_acclimatisation` | Acclimatisation ramp for a new tank |
| `number.<tank>_*` | The effect parameters — moonlight levels and window, cloud frequency, strength and duration, acclimatisation duration and reduction |
| `sensor.<tank>_*` | Current mode, live output level (per channel), programmed level, firmware version, uptime, free memory, operating hours, mesh clients |

Plus services for the lighting programme: `set_daycycle`, `get_daycycle`, `load_scenario`,
`save_scenario`, `preview_curve` and `set_clock`.

## Read this before you use it

**Turning a light on or changing its brightness takes the controller out of its schedule.**
That is how the hardware works: manual levels and the daycycle programme are mutually
exclusive modes, and setting a level has no effect unless the controller is in manual mode.
So `light.turn_on` switches it to manual, and it *stays* there — the lamp will not resume
the programme on its own.

To hand control back, set `select.<tank>_mode` to **Daycycle**. Every light also carries a
`mode` attribute so you can see which state it is in.

A useful automation pattern is to do a manual override and schedule the return:

```yaml
# Bright light for a water change, back to normal after an hour
- alias: Water change lighting
  triggers:
    - trigger: event
      event_type: water_change_started
  actions:
    - action: light.turn_on
      target: { entity_id: light.reef_tank }
      data: { brightness_pct: 100 }
    - delay: "01:00:00"
    - action: select.select_option
      target: { entity_id: select.reef_tank_mode }
      data: { option: daycycle }
```

**The brightness reading moves on its own.** In daycycle mode the controller reports its
*actual* output, which includes cloud and moonlight modulation. With cloud simulation
running, the level drifts continuously — that is the tank telling you the truth, not the
integration being unstable. `sensor.<tank>_programmed_level` shows what the schedule alone
calls for, if you want to compare.

**The device has no authentication whatsoever.** No password, no token, nothing. Anyone who
can reach it on your network has full control of your aquarium lighting, and that is true
with or without this integration. Keep it on a trusted network segment.

## Installation

### HACS

Add this repository as a custom repository of type *Integration*, install **daytime SC20**,
restart Home Assistant, then add the integration from **Settings → Devices & Services**.

### Manual

Copy `custom_components/daytime_sc20/` into your `config/custom_components/` directory and
restart Home Assistant.

### Setup

You need the controller's IP address or hostname. If mDNS works on your network, the device
answers to `sc20.local`. There is no password to enter.

## Working with the lighting programme

The schedule is a list of up to 30 setpoints, each `[minute_of_day, white, blue, red]`.
Minutes run 0–1440 from midnight and the levels are percentages. The list must be sorted,
must start at minute 0 and must end at minute 1440; the controller interpolates between
setpoints.

```yaml
- action: daytime_sc20.set_daycycle
  data:
    config_entry_id: <your entry id>
    setpoints:
      - [0, 0, 0, 0]          # midnight, dark
      - [420, 0, 0, 0]        # 07:00, still dark
      - [430, 0, 0, 80]       # 07:10, red dawn
      - [480, 100, 100, 100]  # 08:00, full daylight
      - [1080, 100, 100, 100] # 18:00
      - [1140, 0, 0, 80]      # 19:00, red dusk
      - [1150, 0, 0, 0]       # 19:10, dark
      - [1440, 0, 0, 0]       # midnight
```

The previous programme is saved to Home Assistant's storage before it is replaced — the
controller has no undo and does not acknowledge writes, so this is the only way back.

`.scen` files from the vendor's website work directly with `load_scenario`; three of them
are included under [`docs/protocol/scenarios/`](docs/protocol/scenarios/).

## Reverse-engineering tools

`tools/sc20_probe.py` connects to a controller, records every frame, and writes a snapshot
of its configuration. It is how the protocol was confirmed and how to check it again if a
firmware update changes something.

```bash
python tools/sc20_probe.py --host 192.168.1.34                 # read-only
python tools/sc20_probe.py --host 192.168.1.34 --allow-writes  # includes reverted writes
```

Read-only by default. The write phase confirms each command before sending it and always
restores the scheduled mode afterwards, including if it crashes. Captures are written to
`docs/protocol/capture/` with SSIDs, MAC addresses, serials and IP addresses scrubbed, so
they are safe to commit.

## Development

```bash
python -m venv .venv && .venv/bin/pip install aiohttp homeassistant pytest pytest-homeassistant-custom-component ruff
.venv/bin/python -m pytest tests/
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
```

The protocol client under `custom_components/daytime_sc20/api/` has no Home Assistant
imports and can be used on its own.

## Tested against

One SC20 on firmware revision `[23, 15]` (webserver 02.3, website 01.5), with three
channels. Other members of this hardware family — the aquaLEDs HC and HC+, and the EHEIM
variants — speak the same protocol but have six channels and a real moonlight channel;
this integration assumes the SC20's three-channel model and does not support them.

## Licence and provenance

This is unofficial and not affiliated with daytime or aquaLEDs.de UG. The protocol
documentation in `docs/protocol/` describes an interface recovered by observing a device
the author owns, for the purpose of interoperability. No vendor firmware or vendor code is
redistributed here.
