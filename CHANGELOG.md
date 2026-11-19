# Changelog

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
