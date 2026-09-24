# Controller Architecture

```
USB / XInput device
      │  ControllerBackend (per OS)        linux-joystick │ xinput │ sdl2 (planned) │ mock
      ▼
PadState  — physical, Xbox-layout names: A B X Y LB RB LT RT BACK START GUIDE LS RS DPAD_*,
            axes LX LY RX RY LT RT (−1…1, triggers 0…1), unknown buttons as BUTTON_n
      │  InputMapping (JSON, input-mappings/*.json, user-selectable)
      ▼
Logical NFNF buttons — A B X Y L R START SELECT UP DOWN LEFT RIGHT
      │
      ▼
Emulator input (libretro joypad ids) — the emulator's own controller config is never used
```

Code: `nfnf_ironmon/controllers/`. Classes:

| Class | Role |
|---|---|
| `ControllerDevice` | One detected device: id, name, backend, kind (`xinput` / `gamepad` / `unknown`), `is_gamepad`, vendor/product, driver, button and axis counts, notes |
| `ControllerBackend` / `OpenController` | Per-OS discovery and non-blocking polling into a `PadState` |
| `InputMapping` | Physical → logical translation, with a stick deadzone, a trigger threshold and cancellation of opposite directions |
| `MappingRepository` | Loads `input-mappings/<id>.json` (validated) |
| `ControllerManager` | Backend status, device discovery, selection (preferred device → XInput pads → other pads; non-gamepads ignored), `poll_logical()`, `test()` |

## Backends

| Backend | OS | How | Status |
|---|---|---|---|
| `linux-joystick` | Linux | Kernel joystick API: `/dev/input/js*`, `ioctl` for name, axes and buttons, 8-byte `js_event` reads. Standard library only. Xbox pads (the `xpad` driver, Microsoft vendor `045e`) are recognized and decoded with the fixed xpad layout (hat → D-pad, triggers 0…1). Other pads report raw `BUTTON_n`. | **READY**. Tested with simulated kernel events through a pipe. Real enumeration works on this host: the only node is a virtual "fake mouse" (3 buttons), which is correctly classified *not a gamepad*. No real Xbox pad was available to test with. |
| `xinput` | Windows | `XInputGetState` via `ctypes` on `xinput1_4.dll` (then `1_3`, `9_1_0`). 4 slots. Covers Xbox 360 / One / Series and XInput-compatible pads. | **IMPLEMENTED, UNTESTED on Windows**. Unit-tested with a fake DLL (buttons, triggers, stick Y inversion, disconnect). |
| `sdl2` | both | SDL2 GameController: hot-plug, a large mapping database, generic HID pads | **PLANNED** (Phase 3, together with the SDL2 game window). `doctor` reports whether PySDL2 is importable. |
| `mock` | any | Injected states for tests | test only |

Xbox 360, One and Series pads map the same way on both OSes because every backend emits the same Xbox-layout `PadState`.

## Default mappings (shipped)

| Physical | `xbox-gba-labels` (default) | `xbox-gba-positional` |
|---|---|---|
| A | A | B |
| B | B | A |
| LB / LT | L | L |
| RB / RT | R | R |
| START | START | START |
| BACK (View) | SELECT | SELECT |
| D-pad / left stick | UP DOWN LEFT RIGHT | same |
| X, Y | unmapped (GBA has no X/Y) | unmapped |

Mappings are data, so new files can target other layouts or map raw `BUTTON_n` for non-Xbox pads. The selected mapping is stored as a user override: `config/settings.json → controller.mapping`.

## Using it

```bash
python3 -m nfnf_ironmon controller list      # backends + devices (+ why a node was ignored)
python3 -m nfnf_ironmon controller mappings
python3 -m nfnf_ironmon controller test --seconds 10   # live physical → NFNF view
```

The UI has a **CONTROLLER** panel (Device, Status, Mapping) with **TEST CONTROLLER**, which shows the live logical buttons for 10 s, and **CONFIGURE**, which chooses the mapping. The local API has `GET /api/controllers`. Each new run records a `CONTROLLER_PREPARED` event (the selected device, mapping and backend status); "no gamepad" never blocks a run.

## Gaps

* No real Xbox controller has been tested yet (none was connected). This is the first thing to check when one is plugged in: `controller list`, then `controller test`.
* Linux device nodes may need the user to be in the `input` group. The backend reports "cannot open … (check 'input' group membership)" instead of failing.
* A keyboard mapping for the game window arrives with the Phase 3 window.
