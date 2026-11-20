# aiodynet

An asyncio client for the Signify Dynalite **DyNet** lighting control bus,
through a DyNet TCP gateway (for example a PDEG).

aiodynet only reads and writes the bus. It has no idea what your site looks
like: there are no devices, rooms, names or templates here, only the areas,
channels, presets and levels that travel on the wire. That makes it a small,
predictable base for higher-level code such as the Home Assistant `dynalite`
integration.

- Typed, immutable messages that validate themselves, so an out-of-range value
  fails where you wrote it, not on the bus
- One connection object with a clear lifecycle: connect, reconnect on its own,
  close cleanly
- Writes are paced, because gateways drop packets that arrive too quickly
- No dependencies outside the standard library. Fully typed (`py.typed`) and
  checked with `mypy --strict`
- A fake gateway in `aiodynet.testing` for testing your own code without
  hardware

Requires Python 3.13 or newer.

## Quick start

```python
import asyncio

from aiodynet import DynetClient, Level, PresetSelect, Received, SetChannelLevel


def show(received: Received) -> None:
    print(received.raw.hex(), received.message)


async def main() -> None:
    async with DynetClient("192.0.2.10", 50000) as client:
        client.subscribe(show)

        # Recall preset 1 in area 12 over two seconds
        await client.send(PresetSelect(area=12, preset=1, fade=2))

        # Fade channel 3 of area 12 to 40 %
        await client.send(
            SetChannelLevel(area=12, channel=3, level=Level.from_percent(40), fade=1)
        )

        await asyncio.sleep(60)  # watch the bus for a minute


asyncio.run(main())
```

## Concepts

### Packets

A `Packet` is one 8-byte DyNet 1 frame. You rarely build one by hand, but it
is always available: every received message comes with the exact packet, and
`send` accepts packets as well as messages.

```python
from aiodynet import Packet

Packet.from_hex("1c 0c 64 00 00 00 ff")  # checksum added for you
Packet.from_hex("1c0c64000000ff75")  # checksum checked
Packet.from_bytes(raw_8_bytes)  # raises InvalidPacketError if bad
```

### DyNet 2 frames

Gateways also send variable-length DyNet 2 frames on the same TCP stream: a
greeting after every new connection, and device sign-on replies. Each starts
with `0xAC`, is `(byte 1 + 1) * 4` bytes long and ends in a two-byte
Fletcher-16 checksum (taken mod 256). A `Dynet2Frame` holds one raw:

```python
from aiodynet import Dynet2Frame

frame = Dynet2Frame.from_hex("ac 02 80 da 00 01 b3 00 2a 0a")  # checksum added
frame.body  # the bytes between the length byte and the checksum
```

Frames are handled like packets everywhere. `PacketFramer.feed` yields both in
the order they arrived, `DynetClient.subscribe` callbacks get both, and
`decode` takes either. A frame decodes to a `Dynet2Message`: the one typed so
far is `Dynet2SignonReply`, and anything else is `UnknownDynet2Message`.
DyNet 2 messages only come from devices; aiodynet can't send them.

To find out what a device is, ask it to sign on:

```python
reply = await client.query(RequestSignon(device_code=0xB3, box=42))
print(f"v{reply.firmware_version}, packets up to {reply.max_rx_size} bytes")
```

### Messages

Each message class maps to exactly one wire form. Fields are keyword-only and
checked when the object is built. Channels and presets are numbered from 1, as
in the DyNet tools and on keypads.

| Message | Opcode(s) | Meaning |
|---|---|---|
| `PresetSelect` | 0x00–0x03, 0x0A–0x0D | Select preset 1–2048 in an area, with a fade of up to 1310.7 s |
| `LinearPresetSelect` | 0x65 | Select preset 1–256 using the one-byte form some controllers send |
| `PresetReport` | 0x62 | An area reports its current preset |
| `RequestPreset` | 0x63 | Ask an area for its current preset |
| `SetChannelLevel` | 0x80–0x83 | Fade one channel (1–1024) to a level, with a fade of up to 5.1 s |
| `ChannelLevelReport` | 0x60 | A channel reports its target and actual level |
| `RequestChannelLevel` | 0x61 | Ask a channel for its level |
| `FadeChannelToPreset` | 0x6B | Fade one channel, or every channel, to a preset's level |
| `AreaCommand` | 0x05, 0x06, 0x08, 0x15, 0x16, 0x66 | Area commands with no data: step the area level down or up, program the current preset, disable or enable panels, save the current preset (`AreaCommandType`) |
| `AreaOff`, `ResetPreset`, `Panic`, `Unpanic` | 0x04, 0x0F, 0x17, 0x18 | Area commands with a preset-style fade |
| `AreaToLevel` | 0x79 | Fade every channel in an area to one level |
| `ChannelOff`, `ChannelOn` | 0x74, 0x75 | Fade a channel, or every channel, to off or full |
| `StopFade` | 0x76 | Stop a channel, or every channel, mid-fade |
| `FadeChannelToLevel` | 0x71–0x73 | The newer channel-level opcodes; `scale` names which one |
| `RestoreSavedPreset` | 0x67 | Return to the preset saved with `SAVE_CURRENT_PRESET` |
| `ProgramLevelsToPreset` | 0x09 | Store an area's current levels as a given preset (overwrites it) |
| `MotionActivitySync` | 0x2E | A motion sensor sharing its activity timer |
| `AreaCascadingSync` | 0x2F | An area's cascading timer keeping linked areas in step |
| `HoldTimerAllPresets` | 0x30 | Suspend or resume the hold timer for every preset (state kept raw) |
| `OccupancyDetectionAllPresets` | 0x31 | Suspend or resume occupancy detection for every preset |
| `SuspendOccupancyDetection`, `ResumeOccupancyDetection` | 0x3A, 0x3B | The same for the current preset only |
| `SetUserPreference`, `RequestUserPreference`, `UserPreferenceReport` | 0x48–0x4A | Device-specific 16-bit preference values. For preference 0x0C, `UserPreferenceReport.temperature` gives °C |
| `RequestPresetLevel`, `PresetLevelReport` | 0x91, 0x92 | The level a channel has stored for a preset |
| `DeviceIdentify` | physical 0x00 | A device saying what it is, with its firmware version |
| `DeviceStatusReport` | physical 0x41 | A device status report; the data bytes are kept raw |
| `ClockTime`, `ClockDate` | physical 0x50, 0x53 | A device setting or broadcasting the time and date |
| `RequestFirmwareVersion` | physical 0x80 | Ask a device for its firmware version. It answers with a `DeviceIdentify` |
| `RequestSignon` | physical 0x40, sub-opcode 0x0A | Ask a device to sign on. It answers with a DyNet 2 `Dynet2SignonReply` |
| `UnknownMessage` | anything else | Passed through unchanged, packet included |

Where an opcode can address one channel or the whole area, `channel=None`
means every channel. Fields whose unit the opcode list doesn't pin down are
kept raw and named `*_raw` (for example `fade_raw`, `time_raw`), rather than
guessed. Area links (0x20–0x23) are left undecoded because the opcode list
doesn't say which bit is which area.

`PresetSelect`, `LinearPresetSelect` and `PresetReport` share the base class
`AreaPresetMessage`, so `isinstance(message, AreaPresetMessage)` catches "this
area is now at preset N" however it was sent.

`decode(packet)` returns the union type `AnyMessage`, so a `match` statement
ending in `assert_never` is checked for exhaustiveness by mypy.

### Levels

DyNet levels run backwards on the wire (0x01 is 100 %, 0xFF is off). `Level`
keeps the raw byte, so it always round-trips, and converts for you:

```python
Level.from_percent(40)
Level.from_fraction(0.4)
Level(0x80)  # the raw byte
Level.OFF, Level.FULL
level.percent, level.fraction, level.is_off
```

Messages accept only `Level`, never a bare number, so a percentage can't be
sent as a raw byte by mistake.

### Fades

Fades are seconds as `float`, rounded to the nearest 20 ms step. A fade that a
message can't carry raises `ValueError` rather than being wrapped or clamped:
`SetChannelLevel` holds up to `MAX_CHANNEL_FADE` (5.1 s) and preset messages up
to `MAX_PRESET_FADE` (1310.7 s).

## The client

```python
client = DynetClient(host, port)
await client.connect()  # raises DynetConnectionError if unreachable
...
await client.close()  # always safe, any number of times
```

or `async with DynetClient(host, port) as client:`.

- **Connecting.** `connect` makes one attempt and raises if it fails, which
  suits "not ready yet, try later" handling. After a successful connect the
  client reconnects on its own after any drop, waiting 1 s at first and up to
  60 s between attempts, until you call `close`.
- **Receiving.** `subscribe(callback)` calls `callback(received)` in the event
  loop for every packet and DyNet 2 frame, where `received.raw` is the
  `Packet` or `Dynet2Frame` as received and `received.message` its decoded
  meaning. It returns an unsubscribe function. A callback that raises is
  logged and doesn't affect the others.
- **Noise.** `subscribe_noise(callback)` calls `callback(data)` with each run
  of bytes that wasn't part of any packet or frame, in stream order, including
  a partial packet cut off by a dropped connection. It's for tools that keep
  a full record of the stream.
- **Connection changes.** `subscribe_connection(callback)` reports drops
  (`False`) and reconnects (`True`). `client.connected` gives the current
  state.
- **Sending.** `await send(message_or_packet)` returns once the packet has been
  written. Packets go out one at a time, 0.2 s apart by default
  (`send_interval`). If the client isn't connected, or the connection drops
  before the packet is written, `send` raises `NotConnectedError`. Nothing is
  kept to send later, so a command can't fire long after it was asked for.
- **Queries.** `await query(RequestPreset(area=12))` sends the request and
  returns the matching `PresetReport`, or raises `TimeoutError`. Likewise a
  `RequestChannelLevel` returns a `ChannelLevelReport`, and a `RequestSignon`
  to one device returns its `Dynet2SignonReply`.
- **Closing.** `close` cancels everything the client started and closes the
  socket. No callbacks run after it begins.
- **Dead gateways.** TCP keepalive is enabled (30 s idle, then 3 probes 10 s
  apart where the OS supports it), so a gateway that vanishes on a quiet bus
  is still noticed.

`check_connection(host, port)` opens and closes a TCP connection without
sending anything. It's meant for validating a host and port in a setup flow.

## Testing your code

`aiodynet.testing.FakeGateway` is a real TCP server on localhost that records
what clients send and sends whatever you tell it to:

```python
from aiodynet import DynetClient, PresetReport, RequestPreset
from aiodynet.testing import FakeGateway


def site(packet):
    if packet == RequestPreset(area=1).to_packet():
        return [PresetReport(area=1, preset=4)]
    return []


async def test_area_one_is_off():
    async with FakeGateway(replies=site) as gateway:
        async with DynetClient(gateway.host, gateway.port) as client:
            report = await client.query(RequestPreset(area=1))
    assert report.preset == 4
```

## How well each claim is verified

DyNet documentation is thin, so this is the evidence for each encoding:

| Encoding | Source |
|---|---|
| Packet layout, checksum | DyNet 1 opcode list; every packet in the test fixtures |
| `PresetSelect` (banks, fade in 20 ms steps, low byte then high byte) | Opcode list, plus byte-for-byte match with the packets `dynalite-devices` 0.1.47 sends |
| `SetChannelLevel` (bank byte offset by one, fade in 20 ms steps) | Byte-for-byte match with `dynalite-devices` 0.1.47 |
| `RequestPreset`, `RequestChannelLevel` | Byte-for-byte match with `dynalite-devices` 0.1.47 |
| `PresetReport`, `LinearPresetSelect`, `ChannelLevelReport`, `FadeChannelToPreset` | Opcode list, plus the meaning `dynalite-devices` 0.1.47 takes from them. Not yet checked against captured traffic |
| `FadeChannelToPreset` fade unit | Unknown, so it's kept as a raw byte |
| `MotionActivitySync`, `UserPreferenceReport`, `ClockTime`, `ClockDate` | Opcode list, plus packets captured on a real site (`tests/fixtures/site-capture.jsonl`). The clock bytes are BCD, and the date packs the day of the week into the top three bits of the month byte; both inferred from that capture |
| All other extended messages | Byte layout from the opcode list only |
| `AreaCascadingSync`, `OccupancyDetectionAllPresets` (0x01 = resume), `DeviceIdentify` (version as hex digits), `DeviceStatusReport`, `UserPreferenceReport.temperature` (whole degrees + hundredths) | Opcode list, plus real packets decoded by the official DyNet network monitor. Negative temperatures haven't been seen, so they come back as `None` |
| `HoldTimerAllPresets`, `SuspendOccupancyDetection`, `ResumeOccupancyDetection`, `ProgramLevelsToPreset` | Opcode list only |
| `RequestFirmwareVersion` | Opcode list, plus requests sent to 45 devices at one site. Each answered with a `DeviceIdentify` from its own address, with the version the site's System Builder job records |
| `Dynet2SignonReply` | One frame from a real D5Sen, decoded field by field by the official DyNet network monitor. Telling a sign-on apart from other 0x81 replies by byte 9 = 0x01 rests on that frame plus one non-sign-on reply. At one site the request got no replies when sent to all devices, only when addressed to a single device |
| DyNet 2 framing: `0xAC`, length, Fletcher-16 mod 256 trailer | Three frames captured from a real gateway; all three verify and end exactly where the next DyNet 1 packet starts. Recheck the checksum on any new kind of frame |

The `dynalite-devices` comparison was made black-box: by driving its public
API against a fake gateway and recording the bytes, without reading its source.
Those vectors are in `tests/test_messages.py`.

## Porting from dynalite-devices

aiodynet replaces the bus layer of `dynalite-devices`. The mapping of areas
and channels to entities, and naming, stay in the integration. To keep existing
installs behaving the same:

- **Brightness.** `dynalite-devices` sends HA brightness `b` (0–255) as the raw
  level `max(1, 255 - b)`, and reads it back as `255 - raw`. Use
  `Level(max(1, 255 - b))` and `255 - level.raw`, not `from_fraction`, to keep
  the same bytes.
- **Channel fades** longer than 5.1 s were clamped to 5.1 s. aiodynet raises
  instead, so clamp with `min(fade, MAX_CHANNEL_FADE)` first.
- **Preset fades** longer than 1310.7 s crashed `dynalite-devices` while it
  built the packet. aiodynet raises a clear `ValueError` at the call.
- **Pacing.** `dynalite-devices` spaced writes 0.2 s apart. That's the default
  here too.
- **Queued writes.** `dynalite-devices` queued writes while disconnected.
  aiodynet raises `NotConnectedError` instead. Decide in the integration
  whether to surface it or retry.

## Development

```sh
uv sync
uv run pytest --cov      # 100 % branch coverage is enforced
uv run ruff check && uv run ruff format --check
uv run mypy
```

To cut a release, see [RELEASING.md](https://github.com/sdunster/aiodynet/blob/main/RELEASING.md).

## Licence

MIT. Not affiliated with or endorsed by Signify.
