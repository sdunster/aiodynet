# Changelog

## Unreleased

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
