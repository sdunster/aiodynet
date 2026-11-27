# Changelog

## 0.5.0 (2026-11-28)

Fixes packets lost when a client closes, types the memory read error, and
keeps byte 2 of area commands.

- **Fix:** `DynetClient.close` could lose packets sent just before it. It
  closed the socket at once, and the gateway sometimes discarded the last
  packet: 39 of 120 short connections (connect, send one packet, close) never
  reached the bus. `close` now half-closes and waits for the gateway to close
  its side, up to the new `close_timeout` (2 s by default); a real gateway
  took 0.7-1.0 s, and 100 of 100 such connections then got through. So
  `close`, and leaving `async with`, now takes about a second while
  connected. Queued packets not yet written fail at the start of `close`.
- `AreaCommand.channel_raw` keeps byte 2, so System Builder's enable and
  disable panels (`1c AREA ff 15/16 …`) decode and re-encode unchanged; it
  used to come back with 0 there. The default is 0, so the bytes sent for an
  `AreaCommand` without it don't change. Panels act on either form.
- `Dynet2MemoryErrorReply` types a device refusing a `Dynet2ReadMemory`:
  a 0x9B frame with 0x10 set in the selector's first byte and one data byte,
  the error code. `MemoryErrorCode.MEMORY_DOES_NOT_EXIST` (19) is System
  Builder's "Failure to read - Memory does not exist", sent for a start
  address past the end of the memory. These frames used to decode as
  `Dynet2MemoryData` with `selector_raw` `10 00`.
- `DynetClient.query` raises `MemoryReadError` (a `DynetError`, with the
  reply in `reply`) when the device refuses a `Dynet2ReadMemory`. It used to
  ignore the refusal and raise `TimeoutError`.

## 0.4.0 (2026-11-27)

Types the device-level traffic decoded with System Builder at one site:
device status and identity, physical channel levels, sensor readings,
memory, DALI tunnelling, colour temperature, and more area commands.

- **Breaking:** `ResetPreset` is renamed `RecallPreviousPreset`, and
  `Opcode.RESET_PRESET` is `Opcode.RECALL_PREVIOUS_PRESET`, after System
  Builder's name for opcode 0x0F, "Recall Previous Preset". The bytes are
  unchanged.
- `DynetClient.query` waits for the answers to `RequestDeviceStatus`,
  `Dynet2RequestSerialNumber`, `Dynet2DeviceRequest` (sign-on, bootloader
  sign-on, build numbers, PCB version), `Dynet2ReadMemory` and the
  light-level `Dynet2StatusRequest`. A request that gets no answer raises
  `ValueError`. A `Dynet2RequestChannelLevel` waits for the
  `Dynet2ChannelLevelReport` for its area and channel.
  `query(RequestSignon(...))` now skips a bootloader sign-on
  reply from the same device.
- `FakeGateway(frame_replies=...)` answers DyNet 2 frames that clients send,
  as `replies` does for packets, and `received_frames` records them.
- `RequestDeviceStatus` types physical 0x40 Request Device Status for every
  status type except sign-on (still `RequestSignon`). Those packets used to
  decode as `UnknownMessage`. `DeviceStatusType` names the known types:
  `DO_D2_SIGNON` (0x0A) and `BOOTLOADER_RUNNING_REASON` (0x16).
- `DeviceStatusReport.application_running` reads a bootloader-running-reason
  report: data byte 0x55 (`APPLICATION_RUNNING`) means the application, not
  the bootloader, is running.
- `DeviceIdentify.after_reset` is true when `boot_raw` is 0x80
  (`DEVICE_RESET`), which System Builder shows as "Device Reset".
- `SetPhysicalChannelLevel` types physical 0x92 "Set Physical Channel
  Level": one channel of one device, not a logical area. Its level is not
  inverted (0 off, 0xFE = `PHYSICAL_LEVEL_FULL` = 100 %), and
  `from_percent` builds one. The fade byte is kept raw (only 0 seen).
- DyNet 2 device commands. `Dynet2DeviceRequest` is a request (0x80)
  carrying one `Dynet2Command`: `SIGNON`, `BOOTLOADER_SIGNON`,
  `FIRMWARE_BUILD`, `BOOTLOADER_BUILD`, `PCB_VERSION`, `SILENCE` (System
  Builder's "flash": the device blinks and stops sending), `GO_BACK_TO_WORK`
  and `RESET`. The serial number request stays `Dynet2RequestSerialNumber`.
  The new replies are `Dynet2BuildNumberReply` and `Dynet2PcbVersionReply`,
  and `Dynet2SignonReply.bootloader` marks the bootloader's sign-on (byte 9
  = 0x02), which used to decode as `UnknownDynet2Message`. Code that matches
  exhaustively on `AnyDynet2Message` needs cases for the new classes.
- Device memory over DyNet 2: `Dynet2ReadMemory` (0x9A) asks for bytes,
  `Dynet2MemoryData` (0x9B) carries them, `Dynet2WriteMemory` (0x9C) writes
  and `Dynet2WriteMemoryAck` (0x9D) acknowledges. `MAIN_EEPROM` is the
  selector System Builder calls "main device, memory type EEPROM", and
  `MAX_MEMORY_CHUNK` (111) is the most data that fits a 128-byte frame.
  These frames used to decode as `UnknownDynet2Message`.
- `Dynet2StatusRequest` types DyNet 2 status polls (0x82), with
  `StatusQuery` naming the light level (answered by a
  `PhysicalStateReport`), the raw light level, the motion power level and
  "Who are you". **The gateway's greeting now decodes as a
  `Dynet2StatusRequest`** for `WHO_ARE_YOU`, not as `UnknownDynet2Message`.
- `Dynet2TunnelData` types DyNet 2 tunnelled data (0xA3). Its `dali`
  property reads System Builder's DALI commands (`DaliTunnelData`), and
  `motion_event_counts` reads the motion statistics a D5Sen broadcasts every
  five minutes once they are turned on (`MotionEventCounts`).
- `RawLightLevelReport` types physical 0x0A, a D5Sen's filtered raw
  illuminance, its answer to the raw light level status poll.
- `Dynet2RequestChannelLevel` (0x22) and `Dynet2ChannelLevelReport` (0x23)
  type DyNet 2's "Request/Reply Channel Level and Temperature" for a
  logical channel. The report carries current and target levels (0-0xFE,
  not inverted) and current and target colour temperatures in kelvin, from
  tunable-white DALI fittings.
- `Dynet2FadeLevelTemperature` (0x10) types DyNet 2's "Fade to level and
  Temperature": fade a logical channel to a level and a colour temperature
  in kelvin, as System Builder does for tunable-white fittings.
- More logical opcodes, each confirmed with one packet System Builder sent
  and labelled: `LightControlAllPresets` (0x11), `SetJoinLevel` (0x14),
  `SetAreaLinks` / `ClearAreaLinks` / `AreaLinksReport` /
  `RequestAreaLinks` (0x20-0x23), `SetRMask` (0x40), `RampToLevelExceptOff`
  (0x5F) and `SetPresetBank` (0x64). They used to decode as `UnknownMessage`.
- `PhysicalStateReport` types physical 0xB8 "Reply Present Physical State",
  a sensor reading; `lux` gives the light level (type 0x01).

## 0.3.0 (2026-11-27)

- `DynetClient.send` also takes DyNet 2 messages (`Dynet2Message`) and raw
  `Dynet2Frame`s. They share the packet queue and pacing. A real gateway
  passes them on to its other clients, and System Builder decodes them.
- `PacketFramer` now waits for DyNet 2 frames of up to 128 bytes by default,
  not 64. Devices report 128 bytes as their largest packet, and System
  Builder's device memory writes use 128-byte frames, which the old limit
  turned into noise. The cost: a stray 0xAC byte in front of a 0x1C packet
  now holds packets back until 116 bytes have arrived, where before they came
  out at once. Pass `max_dynet2_length=64` for the old behaviour.
- `Dynet2RequestSerialNumber` and `Dynet2SerialNumberReply` type the DyNet 2
  serial number request and reply (type 0x0A), which `decode` used to return
  as `UnknownDynet2Message`. Code that matches exhaustively on
  `AnyDynet2Message` needs a case for them.
- `Dynet2SignonReply` also decodes the 44-byte sign-on, which adds an IPv6
  address (`ipv6_address`, with the extra bytes in `extension_raw`). This
  frame used to decode as `UnknownDynet2Message`. Its source is the
  all-devices address, so `DynetClient.query` can't match it to a request.
- Documented that a `DeviceIdentify` answers `RequestFirmwareVersion`
  (checked on hardware).

## 0.2.0 (2026-11-19)

DyNet 2 frames (sync byte 0xAC) are read alongside DyNet 1 packets.

- `Dynet2Frame` holds one frame raw, with its checksum checked.
  `PacketFramer.feed` yields frames as well as packets, in arrival order,
  stepping over each frame whole instead of treating it as noise.
- `decode` takes a packet or a frame. Frames decode to a `Dynet2Message`:
  `Dynet2SignonReply` (a device's address, firmware version and maximum
  packet sizes) or `UnknownDynet2Message`. `AnyMessage` now covers both, and
  `AnyDynet1Message` / `AnyDynet2Message` name each half.
- `RequestSignon` (physical 0x40, sub-opcode 0x0A) asks a device to sign on,
  and `DynetClient.query` waits for its `Dynet2SignonReply`.
  `DYNET2_ALL_DEVICES` is the DyNet 2 all-devices address.
- `PacketFramer(on_discard=...)` is called with each run of skipped bytes, so
  tools can keep noise as evidence rather than only counting it.
- `PacketFramer` drops bytes that can't start a packet straight away, so
  `discarded` and `pending` update sooner. No packets are gained or lost.
- `DynetClient.subscribe_noise` passes on bytes that weren't part of any
  packet or frame, including a partial packet cut off by a disconnect.
- `PacketFramer.reset` reports the partial packet or frame it drops through
  `on_discard` and `discarded`.

More typed messages, from the opcode list and checked against the
official DyNet network monitor where possible:

- Logical: `ProgramLevelsToPreset` (0x09), `AreaCascadingSync` (0x2F),
  `HoldTimerAllPresets` (0x30), `OccupancyDetectionAllPresets` (0x31),
  `SuspendOccupancyDetection` / `ResumeOccupancyDetection` (0x3A, 0x3B).
- Physical: `DeviceIdentify` (0x00), `DeviceStatusReport` (0x41),
  `RequestFirmwareVersion` (0x80).
- `UserPreferenceReport.temperature` reads preference 0x0C as °C.

Breaking:

- `Received.packet` is now `Received.raw`, a `Packet` or `Dynet2Frame`.
- `PacketFramer.feed` and `DynetClient.subscribe` callbacks now also get DyNet
  2 frames, decoded as `Dynet2Message`s.

## 0.1.0 (2026-11-18)

First release.

- `Packet`, `PacketFramer`: DyNet 1 framing and checksums
- Typed messages for preset selection and reports, channel levels and their
  reports and requests, and fading a channel to a preset; anything else is
  passed through as `UnknownMessage`
- `Level` and fade handling with range checks
- `DynetClient`: connect, automatic reconnect with backoff, paced writes,
  subscriptions, `query`, and a clean `close`
- `check_connection` for validating a host and port
- `aiodynet.testing.FakeGateway` for tests
- Typed messages for every other opcode whose layout the DyNet opcode list
  defines clearly: area commands, area off/reset/panic, area and channel
  fades, stop fade, the 0x71-0x73 channel levels, motion sync, user
  preferences, preset levels, and the physical clock time and date
  broadcasts
