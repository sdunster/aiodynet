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
`decode` takes either. A frame decodes to a `Dynet2Message`:

| Message | Frame | Meaning |
|---|---|---|
| `Dynet2SignonReply` | reply (0x81), type 0x01 or 0x02 | A device's address, firmware version and maximum packet sizes, in answer to `RequestSignon` or a sign-on `Dynet2DeviceRequest`. Type 0x02 (`bootloader`) gives the bootloader's version. A 44-byte form also carries an IPv6 address |
| `Dynet2RequestSerialNumber` | request (0x80), type 0x0A | Ask a device for its serial number. System Builder sends these |
| `Dynet2SerialNumberReply` | reply (0x81), type 0x0A | A device's serial number |
| `Dynet2DeviceRequest` | request (0x80), any other type | A `Dynet2Command` for one device: sign on, bootloader sign-on, firmware or bootloader build number, PCB version, silence ("flash"), go back to work, reset |
| `Dynet2BuildNumberReply` | reply (0x81), type 0x06 or 0x07 | The firmware's or bootloader's build number |
| `Dynet2PcbVersionReply` | reply (0x81), type 0x0F | The circuit board's version and revision |
| `Dynet2ReadMemory`, `Dynet2MemoryData` | 0x9A, 0x9B | Read bytes of a device's memory (EEPROM), at most `MAX_MEMORY_CHUNK` at a time |
| `Dynet2MemoryErrorReply` | 0x9B | A device refusing a memory read: `MemoryErrorCode.MEMORY_DOES_NOT_EXIST` for an address past the end |
| `Dynet2WriteMemory`, `Dynet2WriteMemoryAck` | 0x9C, 0x9D | Write bytes of a device's memory. This changes its configuration |
| `Dynet2RequestChannelLevel`, `Dynet2ChannelLevelReport` | 0x22, 0x23 | Ask a logical channel (area, channel, join) for its level and colour temperature; the report has current and target levels and kelvin |
| `Dynet2FadeLevelTemperature` | 0x10 | Fade a logical channel to a level and colour temperature (kelvin) |
| `Dynet2TunnelData` | 0xA3 | Data tunnelled through DyNet: System Builder's DALI commands (`dali` gives a `DaliTunnelData`) and a sensor's statistics (`motion_event_counts` gives `MotionEventCounts`) |
| `Dynet2StatusRequest` | 0x82 | Ask a device for a `StatusQuery`, such as a D5Sen's light level (answered by a `PhysicalStateReport`). The gateway's greeting is a "Who are you" request |
| `UnknownDynet2Message` | anything else | Passed through unchanged, frame included |

`send` takes DyNet 2 messages and raw `Dynet2Frame`s as well. A real gateway
passes most frames from one client on to its other clients and to the
devices, which answer them. It answers some itself: "Who are you"
(`StatusQuery.WHO_ARE_YOU`) gets a 0x83 frame back on the asking connection
only, and no other client sees either. It turns System Builder's DyNet 2
"Fade Physical Channel Level" into a physical 0x92 `SetPhysicalChannelLevel`
for everyone else.

```python
await client.send(
    Dynet2Frame.from_hex("ac 04 82 dc 00 5a aa 55 55 00 0c 00 00 00 00 00 00 00")
)
```

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
| `AreaCommand` | 0x05, 0x06, 0x08, 0x15, 0x16, 0x66 | Area commands with no data: step the area level down or up, program the current preset, disable or enable panels, save the current preset (`AreaCommandType`). Byte 2 is kept as `channel_raw` (0 by default; System Builder sends 0xFF for panel commands) |
| `AreaOff`, `RecallPreviousPreset`, `Panic`, `Unpanic` | 0x04, 0x0F, 0x17, 0x18 | Area commands with a preset-style fade. System Builder calls 0x0F "Recall Previous Preset", 0x17 "Disable Panels and recall Panic Preset" and 0x18 "Enable Panels and recall Previous Preset" |
| `AreaToLevel` | 0x79 | Fade every channel in an area to one level |
| `ChannelOff`, `ChannelOn` | 0x74, 0x75 | Fade a channel, or every channel, to off or full |
| `StopFade` | 0x76 | Stop a channel, or every channel, mid-fade |
| `FadeChannelToLevel` | 0x71–0x73 | The newer channel-level opcodes; `scale` names which one |
| `RestoreSavedPreset` | 0x67 | Return to the preset saved with `SAVE_CURRENT_PRESET` |
| `ProgramLevelsToPreset` | 0x09 | Store an area's current levels as a given preset (overwrites it) |
| `MotionActivitySync` | 0x2E | A motion sensor sharing its activity timer |
| `AreaCascadingSync` | 0x2F | An area's cascading timer keeping linked areas in step |
| `HoldTimerAllPresets` | 0x30 | Suspend or resume the hold timer for every preset (state kept raw) |
| `LightControlAllPresets` | 0x11 | Suspend or resume light level control for every preset |
| `SetPresetBank`, `SetRMask`, `SetJoinLevel` | 0x64, 0x40, 0x14 | Set an area's preset bank, RMask, or smart panels' join level |
| `SetAreaLinks`, `ClearAreaLinks`, `RequestAreaLinks`, `AreaLinksReport` | 0x20–0x23 | Set, clear, ask for and report an area's 24 link bits |
| `RampToLevelExceptOff` | 0x5F | Ramp channels to a level, leaving out channels at 0 % (level and ramp rate kept raw) |
| `OccupancyDetectionAllPresets` | 0x31 | Suspend or resume occupancy detection for every preset |
| `SuspendOccupancyDetection`, `ResumeOccupancyDetection` | 0x3A, 0x3B | The same for the current preset only |
| `SetUserPreference`, `RequestUserPreference`, `UserPreferenceReport` | 0x48–0x4A | Device-specific 16-bit preference values. For preference 0x0C, `UserPreferenceReport.temperature` gives °C |
| `RequestPresetLevel`, `PresetLevelReport` | 0x91, 0x92 | The level a channel has stored for a preset |
| `DeviceIdentify` | physical 0x00 | A device saying what it is, with its firmware version. `after_reset` is true when it was sent on start-up after a reset |
| `RequestDeviceStatus` | physical 0x40 | Ask a device for a kind of status (`DeviceStatusType`), except sign-on. It answers with a `DeviceStatusReport` |
| `DeviceStatusReport` | physical 0x41 | A device status report; the data bytes are kept raw. `application_running` reads the bootloader-running-reason report |
| `SetPhysicalChannelLevel` | physical 0x92 | Set one channel of one device to a level (0 off to 0xFE = 100 %, not inverted). System Builder's "flash channel" |
| `PhysicalStateReport` | physical 0xB8 | A sensor reading, such as the light level in lux (`lux`) |
| `RawLightLevelReport` | physical 0x0A | A sensor's filtered raw light level (unitless) |
| `ClockTime`, `ClockDate` | physical 0x50, 0x53 | A device setting or broadcasting the time and date |
| `RequestFirmwareVersion` | physical 0x80 | Ask a device for its firmware version. It answers with a `DeviceIdentify` |
| `RequestSignon` | physical 0x40, sub-opcode 0x0A | Ask a device to sign on. It answers with a DyNet 2 `Dynet2SignonReply` |
| `UnknownMessage` | anything else | Passed through unchanged, packet included |

Where an opcode can address one channel or the whole area, `channel=None`
means every channel. Fields whose unit the opcode list doesn't pin down are
kept raw and named `*_raw` (for example `fade_raw`, `time_raw`), rather than
guessed. Area links (0x20–0x23) keep their 24 link bits as one number,
`bits`, in the opcode list's order (bits 1–8 in byte 2): which area each bit
links depends on the site.

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
  written. Packets and DyNet 2 frames go out one at a time, 0.2 s apart by default
  (`send_interval`). If the client isn't connected, or the connection drops
  before the packet is written, `send` raises `NotConnectedError`. Nothing is
  kept to send later, so a command can't fire long after it was asked for.
- **Queries.** `await query(RequestPreset(area=12))` sends the request and
  returns the matching `PresetReport`, or raises `TimeoutError`. Likewise a
  `RequestChannelLevel` returns a `ChannelLevelReport`, a `RequestSignon` to
  one device its `Dynet2SignonReply`, and a `RequestDeviceStatus` its
  `DeviceStatusReport`. For DyNet 2: a `Dynet2RequestSerialNumber` returns
  the `Dynet2SerialNumberReply`; a `Dynet2DeviceRequest` the sign-on,
  build-number or PCB reply to its command; a `Dynet2ReadMemory` the
  `Dynet2MemoryData`, or raises `MemoryReadError` if the device refuses it;
  a light-level `Dynet2StatusRequest` the
  `PhysicalStateReport`; and a `Dynet2RequestChannelLevel` the
  `Dynet2ChannelLevelReport`. Requests that get no reply (silence, reset) raise
  `ValueError`; `send` them instead.
- **Closing.** `close` half-closes the connection, waits for the gateway to
  close its side (up to `close_timeout`, 2 s by default; a real gateway took
  0.7-1.0 s), then cancels everything the client started. No callbacks run
  after it begins. Closing the socket at once lost packets sent just before:
  in one test, 39 of 120 one-packet connections never reached the bus.
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
| `PresetSelect` (banks, fade in 20 ms steps, low byte then high byte) | Opcode list, plus byte-for-byte match with the packets `dynalite-devices` 0.1.47 sends. System Builder's task compiler agrees: `Preset(P=11)` compiled to bank 1, preset 3; `P=20` to bank 2, preset 4; a 30 s fade to `dc 05` (1500 steps, low byte first) |
| `SetChannelLevel` (bank byte offset by one, fade in 20 ms steps) | Byte-for-byte match with `dynalite-devices` 0.1.47. System Builder's task compiler agrees: `ChannelLevel(C=2, L=50, F=0.7)` compiled to `1c 13 80 81 ff 23 ff`, and channel 5 to bank byte `00` |
| `RequestPreset`, `RequestChannelLevel` | Byte-for-byte match with `dynalite-devices` 0.1.47 |
| `PresetReport`, `LinearPresetSelect`, `ChannelLevelReport`, `FadeChannelToPreset` | Opcode list, plus the meaning `dynalite-devices` 0.1.47 takes from them. Not yet checked against captured traffic |
| `FadeChannelToPreset` fade unit | Unknown, so it's kept as a raw byte |
| `FadeChannelToLevel` 0x72 (fade in seconds; channel byte = channel − 1; inverted level) | System Builder's task compiler: `ChannelLevel(A=19, C=3, L=25, F=10)`, a fade too long for 0x80–0x83, compiled to `1c 13 02 72 c0 0a ff`. 0x71 (tenths) and 0x73 (minutes) are from the opcode list only |
| Area commands, by System Builder's task command names | Tasks written to a panel from System Builder at one site, read back from its memory: `Off` → 0x04, `Down` → 0x05, `Up` → 0x06, `ResetPreset` → 0x0F (`RecallPreviousPreset`), `Panic` → 0x17, `UnPanic` → 0x18, `PESuspend`/`PEResume` → 0x11 with byte 5 = 0/1 (`LightControlAllPresets`), `OccupancySuspend`/`OccupancyResume` → 0x31 with byte 5 = 0/1, `SaveCurrentPreset` → 0x66, `RestoreSavedPreset` → 0x67, `PresetOffset` → 0x64. With no fade given, they used 0x0A (0.2 s in 20 ms steps) in byte 2, except `RestoreSavedPreset`, which put 0x02 in byte 5 |
| `AreaCommand` enable/disable panels with byte 2 = 0x00 | On hardware: `1c 05 00 15 00 00 ff` disabled the panels in area 5 (backlight off, presses ignored) exactly as System Builder's 0xFF form does, and `1c 05 00 16 00 00 ff` enabled them again |
| `AreaOff` (0x04 is "Recall Off Preset", not preset 5) | Opcode list, plus hardware: `1c 13 00 04 00 00 ff` took the area's channel to 0 % while the area still reported preset 1, and the official DyNet network monitor shows it as "Recall Off Preset with a fade of 0.00s". Some other implementations read 0x00–0x03 and 0x0A–0x0D as eight presets per bank and treat 0x04 as preset 5; that is wrong for this opcode |
| `MotionActivitySync`, `UserPreferenceReport`, `ClockTime`, `ClockDate` | Opcode list, plus packets captured on a real site (`tests/fixtures/site-capture.jsonl`). The clock bytes are BCD, and the date packs the day of the week into the top three bits of the month byte; both inferred from that capture |
| All other extended messages | Byte layout from the opcode list only |
| `AreaCascadingSync`, `OccupancyDetectionAllPresets` (0x01 = resume), `DeviceIdentify` (version as hex digits), `DeviceStatusReport`, `UserPreferenceReport.temperature` (whole degrees + hundredths) | Opcode list, plus real packets decoded by the official DyNet network monitor. Negative temperatures haven't been seen, so they come back as `None` |
| `HoldTimerAllPresets`, `SuspendOccupancyDetection`, `ResumeOccupancyDetection`, `ProgramLevelsToPreset` | Opcode list only |
| `LightControlAllPresets`, `SetPresetBank`, `SetRMask`, `SetJoinLevel`, area links, `RampToLevelExceptOff` | Opcode list, plus one packet of each sent by System Builder at one site with its label (for example "Resume Light Control for all Presets" for byte 5 = 1, "Set Area Links Bits 0xFFFFFF"). The link bit order is the opcode list's; only all-ones and all-zeros were seen |
| `RequestDeviceStatus` (0x16), `DeviceStatusReport.application_running`, `DeviceIdentify.after_reset` | Packets sent to and from D5Sen sensors at one site, labelled by System Builder: "Bootloader running Reason" and its answer "This is not the bootloader, this is the application" (0x55); boot byte 0x80 as "Device Reset" |
| `SetPhysicalChannelLevel` | Packets System Builder sent to flash a relay channel and a DALI channel at one site, labelled "Set Physical Channel Level (Channel 3, Level 100%, Fade 0.00s)" for level 0xFE. Only levels 0 and 0xFE and fade 0 have been seen; `percent` assumes a linear scale |
| `RawLightLevelReport` | D5Sen replies to raw light level polls, labelled "Light Level Request, Filtered raw illuminance value (Level: 0x01)". Only levels up to 0x10 seen, so the high byte is assumed |
| `PhysicalStateReport` | D5Sen replies to PE cell polls, labelled "Reply Present Physical State - Light Level 1627 Lux" for ``01 06 5b``. Other state types are unconfirmed |
| `RequestFirmwareVersion` | Opcode list, plus requests sent to 45 devices at one site. Each answered with a `DeviceIdentify` from its own address, with the version the site's System Builder job records |
| `Dynet2SignonReply` | One frame from a real D5Sen, decoded field by field by the official DyNet network monitor. Telling a sign-on apart from other 0x81 replies by byte 9 = 0x01 rests on that frame plus one non-sign-on reply. At one site the request got no replies when sent to all devices, only when addressed to a single device. The 44-byte form comes from one frame, sent by a DALI controller that is also an Ethernet bridge: its 16 extra address bytes were that device's IPv6 address. Its source was the all-devices address, so `query` can't match it to the request |
| `Dynet2RequestSerialNumber`, `Dynet2SerialNumberReply` | Requests from System Builder and the replies to them, captured at one site. The serial numbers in 44 replies were checked against that site's System Builder job: 42 matched, and the other two devices had been replaced since the job was last updated. Byte 10 of the reply and its three trailing zero bytes are kept raw |
| `Dynet2DeviceRequest`, `Dynet2BuildNumberReply`, `Dynet2PcbVersionReply`, `Dynet2SignonReply.bootloader` | Each command sent to a D5Sen (and silence / go back to work to an Antumbra panel) at one site, from System Builder's address, with System Builder labelling the requests and replies. The PCB version matched System Builder's job. Reset and silence were captured from System Builder itself. The build number's byte order is assumed (only build 1 seen) |
| `Dynet2ReadMemory`, `Dynet2MemoryData`, `Dynet2WriteMemory`, `Dynet2WriteMemoryAck` | System Builder reading and writing two D5Sens' configuration at one site, with its labels for the read ("Main device, memory type EEPROM, start address 0x1a, data length 5", "no error"). A read sent by aiodynet got the expected reply. The write layout and acknowledgement are inferred from System Builder's writes, which matched the change made in its user interface. Selector 02 00 was seen once, meaning unknown |
| `Dynet2MemoryErrorReply` | Reads at 0x8000 and 0xFF90 sent by aiodynet to a D5Sen, which System Builder labelled "Data Length : 1, Error code : 19, Detail : Failure to read - Memory does not exist". Only code 19 has been seen. A read starting at 0x7FF8 for 16 bytes got 16 bytes of 0xFF and no error, so the device checks only the start address |
| `Dynet2StatusRequest`, `StatusQuery` | System Builder's polls of two D5Sens at one site, with its labels, and polls sent from aiodynet's side: the PE cell light-level poll got the `PhysicalStateReport` System Builder showed as the lux reading. The motion power-level poll never got an answer. "Who are you" was sent with four address variants; only the gateway answered |
| `Dynet2RequestChannelLevel`, `Dynet2ChannelLevelReport` | One request from System Builder and a DALI controller's reply at one site, labelled field by field ("Current Level 50%, Target Level 50%, Current Temperature 3205 Kelvin, Target Temperature 3200 Kelvin"). Both levels were equal, so which comes first is assumed; byte 8 (0x03) and the report's last 10 bytes (zero) are kept raw |
| `Dynet2FadeLevelTemperature` | Five frames System Builder sent to tunable-white DALI downlights at one site (3490 K, 4491 K and 5990 K at 50 %, then 90 % and 100 %), one labelled field by field; the next report showed the new target temperature. Only 2 s fades were seen, so the 10 ms fade step is assumed; bytes 8, 12 and 13 are kept raw |
| `Dynet2TunnelData`, `DaliTunnelData`, `MotionEventCounts` | DALI commands System Builder sent to a DALI controller, and a D5Sen's metric broadcasts, at one site, with System Builder's field-by-field labels. The metric header comes from one frame; two of its bytes are unexplained and kept raw |
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
