# Changelog

## Unreleased

- DyNet 2 frames (sync byte 0xAC) are recognised: `Dynet2Frame` holds one
  raw, with its checksum checked. `PacketFramer` steps over them whole
  instead of treating them as noise, and yields them from the new
  `feed_all`. `DynetClient.subscribe_dynet2` passes them to callbacks.
  Existing `feed` and `subscribe` callers still get only DyNet 1 packets.
- `PacketFramer(on_discard=...)` is called with each run of skipped bytes, so
  tools can keep noise as evidence rather than only counting it.
- `PacketFramer` drops bytes that can't start a packet straight away, so
  `discarded` and `pending` update sooner. No packets are gained or lost.

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
