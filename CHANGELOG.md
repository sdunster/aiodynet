# Changelog

## 0.1.0 (unreleased)

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
