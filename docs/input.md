# Remote keyboard and mouse (Phase 7)

Use `rdc host --allow-control` with the existing server/monitor/FPS options.
`rdc connect` automatically supports input when the host enables it. Approval
explicitly grants screen viewing AND keyboard/mouse control; the default host
command remains view-only. Native input controllers initialize only after approval.
The host terminal announces active control. Stop it with Ctrl+C to revoke access.

The viewer supports pointer movement, left/middle/right buttons, dragging, vertical
wheel scrolling, printable keys, modifiers, navigation keys, and F1–F12. Escape is
reserved for local disconnect. Focus loss releases held keys and buttons. Keyboard
layouts should match; IME composition, clipboard transfer, OS secure shortcuts
(such as Windows Ctrl+Alt+Delete), and guaranteed key repeat are not supported.

Coordinates are normalized against the displayed video, excluding letterboxing,
and mapped to the selected monitor's dimensions and desktop offset, including
negative offsets and monitor 0's combined desktop. Keep monitor geometry stable
during a session; reconnect after display configuration changes.

## Transport and cleanup

The host creates one reliable, ordered `rdc-input-v1` WebRTC data channel only
when control is enabled and sharing is approved. Input travels over WebRTC's
DTLS/SCTP transport, not through the signaling relay. View-only hosts reject
incoming data channels. The viewer indicates CONTROL ENABLED or VIEW ONLY.

Messages are JSON text, limited to 512 characters, with strict schemas and no
extra fields:

- `move`: normalized finite `x` and `y` in [0, 1].
- `button`: `button` (left/middle/right) and boolean `down`.
- `key`: printable single character or supported named key, and boolean `down`.
- `scroll`: integer `dy` in [-10, 10].
- `reset`: release all remotely held inputs.
- `heartbeat`: keep the input lease alive.

Unknown or malformed input ends the session. Duplicate presses and unmatched
releases are ignored. The host tracks at most 128 held keys. The viewer stops the
session if its outbound input buffer exceeds 64 KiB. Heartbeats run every second;
the host releases held inputs after three seconds without valid input (checked
once per second). Focus loss, channel closure, signaling loss, and normal shutdown
also release held inputs. Abrupt process termination cannot guarantee cleanup.

## Platform support

Input uses [pynput controllers](https://pynput.readthedocs.io/en/stable/index.html).
Windows and Linux X11 are the primary targets. Native Wayland input is rejected
explicitly; use an X11 session. macOS requires the appropriate Accessibility
permission for the host terminal/application. OS security boundaries can prevent
input into elevated applications. See [pynput platform limitations](https://pynput.readthedocs.io/en/stable/limitations.html).

Automated tests exercise protocol validation, viewport mapping, key identity,
pressed-state cleanup, and real local WebRTC delivery with a fake native backend.
A two-machine desktop check is still needed for each target OS: approve control,
type in an editor, use Ctrl shortcuts, drag/select, scroll, resize the viewer,
lose focus with a modifier held, disconnect with a button held, and verify the
host retains no remotely held keys/buttons. Also verify a default view-only host
ignores all viewer input.
