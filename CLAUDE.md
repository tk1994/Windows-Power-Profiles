# Power Manager & Shutdown Timer — Project Summary for Claude Code

## What this is
Two Python scripts for Windows (with cross-platform install hooks) that manage:
1. **Power profiles** — create, switch, and configure Windows power schemes via `powercfg`
2. **Shutdown timer** — enforce scheduled shutdowns with a user-facing delay prompt

All config lives in plain JSON files. A system tray icon (pystray) ties everything together.

---

## File structure
```
power_manager.py          # Main script — tray, profile switching, CLI
power_profiles.json       # Profile definitions (only overrides, base scheme handles defaults)
shutdown_timer.py         # Background shutdown timer service
shutdown_timer.json       # Shutdown timer config (live-reloaded every cycle)
USER_MANUAL.md            # Full usage docs
.power_manager_state.json # Auto-generated — stores Windows scheme GUIDs, do not edit
shutdown_timer.log        # Auto-generated — runtime log from shutdown_timer.py
```

---

## power_manager.py

### CLI
```cmd
python power_manager.py              # launch tray
python power_manager.py install      # register tray at Windows login (registry HKCU Run key) + sync profiles
python power_manager.py uninstall    # remove from login + delete managed schemes + restore Balanced
python power_manager.py enable       # re-register tray at login
python power_manager.py disable      # remove from login startup (keeps profiles intact)
python power_manager.py status       # print tray state, active profile, shutdown timer config
python power_manager.py list         # list profiles and which is active
python power_manager.py apply <name> # switch active Windows power scheme
python power_manager.py toggle       # cycle to next profile
python power_manager.py sync         # push JSON changes to Windows (create schemes if missing)
python power_manager.py remove       # delete all managed schemes, restore Windows Balanced
```

### Key design decisions
- Profiles are cloned from a `base_scheme` GUID via `powercfg /duplicatescheme` — base handles all defaults
- `settings: {}` in JSON = pure clone, no overrides. Only add keys you want to change
- GUIDs of created schemes stored in `.power_manager_state.json`
- Timeouts applied via `powercfg /setacvalueindex` with minutes→seconds conversion (NOT `/change` — avoids temporary scheme activation)
- `hibernate` is a global Windows switch — applied only at `cmd_apply()` time, not at `sync` time
- WiFi power save GUID varies by machine/driver — auto-detected at runtime, skipped gracefully if absent
- `PYTHON_EXE` resolved to real python.exe — rejects Microsoft Store alias stubs (WindowsApps path) which SYSTEM cannot access

### Tray menu structure
```
⚡ Power Profile ►       — profile submenu
Toggle profile (cycle)
─────────────────────
✓/✗ Shutdown Timer ►    — shows "Not installed" if task absent, full controls if installed
─────────────────────
✓ Start tray at login   — toggleable
Reload profiles from JSON
Exit
```

### Shutdown timer section in tray
- Reads `shutdown_task_is_installed()` via `schtasks /query` to determine real state
- If not installed → shows Install option only
- If installed → shows enable/disable/configure/uninstall
- `disable_mode`: `none` | `this_boot` | `permanent`
- Configure window is a dark-themed Tkinter dialog (always on top, centered)

---

## shutdown_timer.py

### CLI
```cmd
python shutdown_timer.py install   # create Windows Scheduled Task (requires admin, SYSTEM account)
python shutdown_timer.py remove    # delete the Scheduled Task + schtasks /enable fix
python shutdown_timer.py run       # run timer loop in foreground
python shutdown_timer.py test      # run timer loop, skip initial sleep (for testing)
python shutdown_timer.py log       # print contents of shutdown_timer.log
```

### Timer loop logic
```
On start:
  - Resolve real Python path (reject WindowsApps alias)
  - Stamp boot_session_id into config
  - If disable_mode == "this_boot" and boot_session_id changed → clear disable

Every cycle:
  - Load config (live reload — changes picked up without restart)
  - Check disable_mode → if disabled, sleep 60s and recheck
  - Sleep delay_hours * 3600
  - Check is_user_logged_in() via `query session`
    - Logged in → show Tkinter prompt with countdown
      - Delay clicked → reset timer
      - Dismissed / timed out → shutdown
    - Not logged in → sleep headless_grace_seconds → shutdown
```

### Scheduled Task
- Name: `ShutdownTimer`
- Runs as SYSTEM (SID `S-1-5-18`) at boot
- Created via XML passed to `schtasks /create`
- After create: runs `schtasks /change /tn ShutdownTimer /enable` to handle pre-existing disabled tasks
- Uses real `python.exe` path resolved at install time

### Config (shutdown_timer.json)
```json
{
  "enabled": true,
  "disable_mode": "none",
  "boot_session_id": "",
  "delay_hours": 1,
  "dialog_timeout_seconds": 60,
  "headless_grace_seconds": 600,
  "shutdown_warning_seconds": 60
}
```
- `boot_session_id` is a UUID written at process start — used to detect new boots and clear `this_boot` disables
- Config is re-read every cycle — edit JSON and it takes effect on the next hour without restarting

### Logging
- All events written to `shutdown_timer.log` with timestamps
- Covers: startup info, config loaded, session detection output, prompt result, shutdown command result

---

## Known issues / gotchas

| Issue | Status |
|---|---|
| Microsoft Store Python alias (WindowsApps) can't run as SYSTEM | Fixed — auto-resolved to real python.exe |
| WiFi power save GUID varies by machine | Fixed — auto-detected, skipped gracefully |
| `powercfg /hibernate` is global not per-scheme | Fixed — applied at activation time only |
| `powercfg /change` requires active scheme | Fixed — timeouts now use `setacvalueindex` directly |
| Pre-existing disabled task not enabling | Fixed — `schtasks /change /enable` after create |
| Tray showed timer as enabled even when task not installed | Fixed — task installed state checked before building label |

---

## Requirements
```cmd
pip install pystray pillow   # tray GUI only; CLI works without these
```
Python must be installed from python.org, not Microsoft Store.

## Admin requirements
Commands needing admin: `install`, `uninstall`, `sync`, `remove` (power_manager); `install`, `remove` (shutdown_timer)
Commands not needing admin: `enable`, `disable`, `status`, `list`, `apply`, `toggle`, tray launch
