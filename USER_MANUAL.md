# Power Manager & Shutdown Timer — User Manual

Two Python scripts that give you full control over Windows power profiles
and scheduled shutdowns, configured through plain text files and a system
tray icon. All files must live in the same folder.

---

## Files

| File | Edit? | Purpose |
|---|---|---|
| `power_manager.py` | No | Main script — tray icon, power profile switching, shutdown timer controls |
| `power_profiles.json` | ✅ Yes | Define and tune your power profiles |
| `shutdown_timer.py` | No | Background shutdown timer service |
| `shutdown_timer.json` | ✅ Yes | Shutdown timer settings (also editable via tray) |
| `.power_manager_state.json` | No | Auto-generated — stores Windows scheme GUIDs, do not edit |

---

## Requirements

```cmd
pip install pystray pillow
```

> `pystray` and `pillow` are only needed for the tray GUI.
> All CLI commands work with Python's standard library alone.

---

## Part 1 — Power Manager

### Commands

```cmd
python power_manager.py                  # launch tray icon
python power_manager.py install          # register tray at login + create profiles in Windows
python power_manager.py uninstall        # remove tray from login + restore default power schemes
python power_manager.py enable           # re-enable tray at login (if previously disabled)
python power_manager.py disable          # stop tray from starting at login (keeps profiles intact)
python power_manager.py status           # show full status — tray, profiles, shutdown timer
python power_manager.py list             # list all profiles and which is active
python power_manager.py apply <name>     # switch to a profile by name
python power_manager.py toggle           # cycle to the next profile
python power_manager.py sync             # push all JSON changes to Windows
python power_manager.py remove           # delete all managed power schemes, restore Windows defaults
```

### First-time setup

Run once as Administrator:

```cmd
python power_manager.py install
```

This:
- Registers the tray to start automatically at Windows login
- Creates all profiles from `power_profiles.json` as real Windows power schemes
- Safe to re-run — updates existing schemes without duplicating them

Then launch the tray immediately (without waiting for reboot):

```cmd
pythonw power_manager.py
```

### Difference between commands

| Command | Tray at login | Power schemes | Notes |
|---|---|---|---|
| `install` | ✅ Registers | ✅ Creates | Full setup |
| `uninstall` | ❌ Removes | ❌ Deletes + restores Balanced | Full teardown |
| `enable` | ✅ Registers | Untouched | Re-enable after disable |
| `disable` | ❌ Removes | Untouched | Stop autostart, keep profiles |
| `remove` | Untouched | ❌ Deletes + restores Balanced | Profiles only, tray untouched |

### Tray menu

```
Right-click lightning bolt icon
│
├── ⚡ Power Profile  ►
│       ├── ✓  Balanced          ← active profile
│       ├──    Performance
│       └──    Battery Saver
│
├── Toggle profile (cycle)
├── ─────────────────────────────
├── ✓/✗  Shutdown Timer — Enabled (1h)  ►
│       ├── Enable
│       ├── Disable until next boot
│       ├── Disable permanently
│       ├── ─────────────────────
│       ├── Configure...
│       ├── ─────────────────────
│       └── Install / Uninstall shutdown timer
│
├── ─────────────────────────────
├── ✓  Start tray at login       ← toggleable
├── Reload profiles from JSON
└── Exit
```

Tray icon color reflects active profile:
- 🟢 Green — Performance
- 🔵 Blue — Balanced
- 🟠 Orange — Battery Saver

### Status command output

```cmd
python power_manager.py status
```

```
====================================================
  Power Manager — Status
====================================================

  Tray at login : YES

  Power profiles:
    Performance     xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    Balanced        xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx  (ACTIVE)
    Battery Saver   xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

  Shutdown Timer:
    Scheduled Task : Installed
    State          : Enabled
    Interval       : 1h
    Prompt timeout : 60s
    Headless grace : 600s
    Shutdown warn  : 60s
```

---

### Editing profiles

Open `power_profiles.json` in any text editor. After saving:

```cmd
python power_manager.py sync
```

Changes apply immediately — no restart needed.

#### Settings reference

**Display / Sleep timeouts** — values in minutes, `0` = never

| Setting | What it controls |
|---|---|
| `monitor_timeout_ac` | Screen off when plugged in |
| `monitor_timeout_dc` | Screen off on battery |
| `standby_timeout_ac` | Sleep when plugged in |
| `standby_timeout_dc` | Sleep on battery |
| `hibernate_timeout_ac` | Hibernate when plugged in |
| `hibernate_timeout_dc` | Hibernate on battery |

**CPU** — values are percentages (0–100)

| Setting | What it controls |
|---|---|
| `processor_min_ac` | Minimum CPU speed on AC |
| `processor_min_dc` | Minimum CPU speed on battery |
| `processor_max_ac` | Maximum CPU speed on AC |
| `processor_max_dc` | Maximum CPU speed on battery — set to 50 to halve speed |

**Hardware**

| Setting | Values | What it controls |
|---|---|---|
| `usb_selective_suspend` | `0` = always on, `1` = power saving | USB device idle power |
| `hibernate` | `0` = off, `1` = on | Master hibernate switch |
| `wifi_power_save` | `0` = max speed → `3` = max saving | WiFi adapter power mode |

**Lid and button actions**

| Setting | What it controls |
|---|---|
| `lid_close_ac` | Lid closed on AC |
| `lid_close_dc` | Lid closed on battery |
| `power_button_ac` | Power button on AC |
| `power_button_dc` | Power button on battery |
| `sleep_button_ac` | Sleep button on AC |
| `sleep_button_dc` | Sleep button on battery |

Action values: `0` = do nothing, `1` = sleep, `2` = hibernate, `3` = shut down

#### Adding a new profile

Add a block inside `"profiles": [ ]` in `power_profiles.json`:

```json
{
  "name": "Meeting",
  "description": "Screen stays on, no sleep",
  "base_scheme": "381b4222-f694-41f0-9685-ff5bb260df2e",
  "settings": {
    "monitor_timeout_ac": 0,
    "monitor_timeout_dc": 0,
    "standby_timeout_ac": 0,
    "standby_timeout_dc": 0,
    "processor_max_dc": 70,
    "wifi_power_save": 0,
    "lid_close_ac": 0,
    "lid_close_dc": 0
  }
}
```

Run `python power_manager.py sync` — it appears in the tray immediately.

**Base scheme GUIDs:**

| Scheme | GUID |
|---|---|
| Balanced | `381b4222-f694-41f0-9685-ff5bb260df2e` |
| High Performance | `8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c` |
| Power Saver | `a1841308-3541-4fab-bc81-f71556f20b4a` |

---

## Part 2 — Shutdown Timer

### How it works

```
Every N hours (default: 1)
    │
    ├── User logged in?
    │       YES → Show popup with countdown (default: 60s)
    │               ├── "Delay 1 Hour" → reset timer
    │               └── Dismissed / timed out / "Shutdown Now" → shutdown
    │
    └── No user logged in → wait headless grace period (default: 10 mins) → shutdown
```

### Commands

```cmd
python shutdown_timer.py install   # create Windows Scheduled Task (run as Administrator)
python shutdown_timer.py remove    # delete the Scheduled Task
python shutdown_timer.py run       # run directly in foreground (for testing only)
```

> `install` and `remove` can also be triggered from the tray menu under the
> Shutdown Timer submenu.

### Cancelling a pending shutdown

If you missed the prompt and a shutdown was triggered:

```cmd
shutdown /a
```

Run this in any terminal before the countdown expires.

### Disable modes

The timer supports three states, configurable from the tray or `shutdown_timer.json`:

| Mode | Behaviour |
|---|---|
| `none` (Enabled) | Timer runs normally every hour |
| `this_boot` | Timer skipped for this boot only — resets automatically on next boot |
| `permanent` | Timer skipped indefinitely until manually re-enabled |

### Editing config

Open `shutdown_timer.json` in any text editor. Changes are picked up
automatically on the timer's next cycle — no restart needed.

| Setting | Default | Description |
|---|---|---|
| `enabled` | `true` | Master on/off switch |
| `disable_mode` | `"none"` | `none` / `this_boot` / `permanent` |
| `delay_hours` | `1` | Hours between shutdown checks |
| `dialog_timeout_seconds` | `60` | Seconds the popup waits for input |
| `headless_grace_seconds` | `600` | Seconds before shutdown when no user logged in |
| `shutdown_warning_seconds` | `60` | OS /t grace period after shutdown is issued |

> `boot_session_id` is auto-managed — do not edit it.

### Full timeline

| Scenario | Timeline |
|---|---|
| No one logs in | Boot → 1h wait → 10 min grace → 1 min Windows warning → off |
| User logs in, ignores prompt | Boot → 1h wait → 60s dialog → 1 min Windows warning → off |
| User clicks Delay | Resets — another 1h wait begins |
| User clicks Shutdown Now | 1 min Windows warning → off |

---

## Permissions

Both scripts work best as Administrator for full control:

```cmd
# Right-click terminal → "Run as administrator"
python power_manager.py install
python shutdown_timer.py install
```

Without admin:
- Power Manager — basic timeouts apply; hibernate and CPU throttle may silently fail
- Shutdown Timer — `install` fails; `run` mode still works for testing

To restore Windows factory power schemes completely:

```cmd
powercfg -restoredefaultschemes
```

---

## Quick reference

```cmd
# Power Manager
python power_manager.py              # launch tray
python power_manager.py install      # register at login + create profiles
python power_manager.py uninstall    # remove from login + restore schemes
python power_manager.py enable       # re-enable at login
python power_manager.py disable      # disable at login (keeps profiles)
python power_manager.py status       # show full status
python power_manager.py list         # list profiles
python power_manager.py apply <name> # switch profile
python power_manager.py toggle       # cycle profiles
python power_manager.py sync         # push JSON edits to Windows
python power_manager.py remove       # delete managed schemes only

# Shutdown Timer
python shutdown_timer.py install     # create Scheduled Task (as Admin)
python shutdown_timer.py remove      # delete Scheduled Task
python shutdown_timer.py run         # run directly (testing only)
shutdown /a                          # cancel a pending shutdown
```
