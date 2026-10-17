"""
power_manager.py — Windows Power Profile Manager
=================================================
Reads profiles from power_profiles.json (same directory as this script),
creates them as real Windows power schemes on first use, and lets you
toggle / apply them from the CLI or a system-tray GUI.

Usage
-----
  python power_manager.py              # launch system-tray GUI
  python power_manager.py list         # list all profiles and which is active
  python power_manager.py apply <name> # apply a profile by name
  python power_manager.py toggle       # cycle to the next profile
  python power_manager.py sync         # re-apply all profile settings from JSON
                                       # (use after editing power_profiles.json)

Editing profiles
----------------
Open power_profiles.json in any text editor.
  • Change timeout/CPU/wifi values and run:  python power_manager.py sync
  • Add a new profile block and run:         python power_manager.py sync
  • Remove a profile block — its Windows scheme stays but won't be managed.

Requirements
------------
  pip install pystray pillow   # for the system-tray GUI only
  # CLI commands work with stdlib alone
"""

import os
import sys
import json
import subprocess
import re
import ctypes
import threading
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR    = Path(__file__).parent
SCRIPT_PATH   = os.path.abspath(__file__)
PROFILES_JSON = SCRIPT_DIR / "power_profiles.json"
STATE_FILE    = SCRIPT_DIR / ".power_manager_state.json"   # stores created GUIDs


def _resolve_real_python() -> str:
    """Return real python.exe, rejecting Microsoft Store alias stubs."""
    candidate = sys.executable
    if "WindowsApps" in candidate:
        try:
            result = subprocess.run(
                ["py", "-3", "-c", "import sys; print(sys.executable)"],
                capture_output=True, text=True
            )
            real = result.stdout.strip()
            if real and "WindowsApps" not in real and os.path.isfile(real):
                return real
        except Exception:
            pass
        search_paths = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python",
            Path("C:/Python313"), Path("C:/Python312"), Path("C:/Python311"),
            Path("C:/Program Files/Python313"), Path("C:/Program Files/Python312"),
        ]
        for base in search_paths:
            for f in base.glob("**/python.exe"):
                if f.is_file() and "WindowsApps" not in str(f):
                    return str(f)
        raise RuntimeError(
            "Could not find a real Python installation.\n"
            "The Microsoft Store Python alias cannot be used for startup registration.\n"
            "Please install Python from https://python.org and re-run install."
        )
    return candidate


PYTHON_EXE = _resolve_real_python()

# ── Powercfg sub-GUIDs needed for CPU/USB/WiFi settings ──────────────────────
# (scheme > subgroup > setting)
POWERCFG_SETTINGS = {
    "processor_min_ac": ("54533251-82be-4824-96c1-47b60b740d00",
                         "893dee8e-2bef-41e0-89c6-b55d0929964c", "ACSettingIndex"),
    "processor_min_dc": ("54533251-82be-4824-96c1-47b60b740d00",
                         "893dee8e-2bef-41e0-89c6-b55d0929964c", "DCSettingIndex"),
    "processor_max_ac": ("54533251-82be-4824-96c1-47b60b740d00",
                         "bc5038f7-23e0-4960-96da-33abaf5935ec", "ACSettingIndex"),
    "processor_max_dc": ("54533251-82be-4824-96c1-47b60b740d00",
                         "bc5038f7-23e0-4960-96da-33abaf5935ec", "DCSettingIndex"),
    "usb_selective_suspend": ("2a737441-1930-4402-8d77-b2bebba308a3",
                               "48e6b7a6-50f5-4782-a5d4-53bb8f07e226", None),
    "wifi_power_save": ("19caa586-e17e-4528-8ec5-4645efb58574",
                        "12bbebe6-58d6-4636-95bb-3217ef867c1a", None),
    # Lid / button actions  (subgroup: 4f971e89-eebd-4455-a8de-9e59040e7347)
    "lid_close_ac":    ("4f971e89-eebd-4455-a8de-9e59040e7347",
                        "5ca83367-6e45-459f-a27b-476b1d01c936", "ACSettingIndex"),
    "lid_close_dc":    ("4f971e89-eebd-4455-a8de-9e59040e7347",
                        "5ca83367-6e45-459f-a27b-476b1d01c936", "DCSettingIndex"),
    "power_button_ac": ("4f971e89-eebd-4455-a8de-9e59040e7347",
                        "7648efa3-dd9c-4e3e-b566-50f929386280", "ACSettingIndex"),
    "power_button_dc": ("4f971e89-eebd-4455-a8de-9e59040e7347",
                        "7648efa3-dd9c-4e3e-b566-50f929386280", "DCSettingIndex"),
    "sleep_button_ac": ("4f971e89-eebd-4455-a8de-9e59040e7347",
                        "96996bc0-ad50-47ec-923b-6f41874dd9eb", "ACSettingIndex"),
    "sleep_button_dc": ("4f971e89-eebd-4455-a8de-9e59040e7347",
                        "96996bc0-ad50-47ec-923b-6f41874dd9eb", "DCSettingIndex"),
}


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  HELPERS
# ╚══════════════════════════════════════════════════════════════════════════════

def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def run(cmd: list, silent=False) -> tuple[int, str, str]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not silent and r.returncode != 0:
        print(f"  [warn] {' '.join(cmd)}\n  {r.stderr.strip()}")
    return r.returncode, r.stdout, r.stderr


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_state() -> dict:
    if STATE_FILE.exists():
        return load_json(STATE_FILE)
    return {"guids": {}}   # profile_name -> GUID


def save_state(state: dict):
    save_json(STATE_FILE, state)


def get_windows_schemes() -> dict[str, str]:
    """Return {guid: name} for all Windows power schemes."""
    _, out, _ = run(["powercfg", "/list"], silent=True)
    schemes = {}
    for line in out.splitlines():
        m = re.search(
            r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s+\((.+?)\)",
            line, re.I
        )
        if m:
            schemes[m.group(1).lower()] = m.group(2).strip()
    return schemes


def get_active_guid() -> str:
    _, out, _ = run(["powercfg", "/getactivescheme"], silent=True)
    m = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        out, re.I
    )
    return m.group(1).lower() if m else ""


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  PROFILE CREATION & APPLICATION
# ╚══════════════════════════════════════════════════════════════════════════════

def ensure_profile_exists(profile: dict, state: dict) -> str:
    """
    Make sure a Windows power scheme exists for this profile.
    Returns its GUID (creates it if needed).
    """
    name = profile["name"]

    # Already created before?
    if name in state["guids"]:
        existing = get_windows_schemes()
        guid = state["guids"][name]
        if guid in existing:
            return guid
        # Scheme was deleted externally — recreate it
        print(f"  [info] Scheme for '{name}' missing from Windows, recreating…")

    base = profile.get("base_scheme", "381b4222-f694-41f0-9685-ff5bb260df2e")
    rc, out, err = run(["powercfg", "/duplicatescheme", base])
    if rc != 0:
        raise RuntimeError(f"Could not duplicate base scheme: {err}")

    m = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        out, re.I
    )
    if not m:
        raise RuntimeError(f"Could not parse new GUID from: {out}")

    guid = m.group(1).lower()
    desc = profile.get("description", "")
    run(["powercfg", "/changename", guid, name, desc])

    state["guids"][name] = guid
    save_state(state)
    print(f"  [info] Created Windows scheme '{name}' → {guid}")
    return guid


def get_wifi_power_save_guids() -> tuple[str, str] | None:
    """
    Detect the WiFi power save subgroup and setting GUIDs on this machine.
    Returns (subgroup_guid, setting_guid) or None if not found.
    The GUIDs vary by Windows version and network adapter driver.
    """
    known_pairs = [
        # Windows 10/11 common
        ("19caa586-e17e-4528-8ec5-4645efb58574",
         "12bbebe6-58d6-4636-95bb-3217ef867c1a"),
        # Older Windows / some OEMs
        ("19caa586-e17e-4528-8ec5-4645efb58574",
         "7f8e99c4-e2fb-4c19-a4c2-4bc547df6ed4"),
    ]
    try:
        _, out, _ = run(["powercfg", "/query"], silent=True)
        for subgroup, setting in known_pairs:
            if subgroup.lower() in out.lower() and setting.lower() in out.lower():
                return subgroup, setting
        # Try parsing from powercfg output — look for "Wireless Adapter Settings"
        lines = out.splitlines()
        in_wireless = False
        subgroup_guid = None
        for line in lines:
            if "wireless" in line.lower() or "wi-fi" in line.lower() or "wlan" in line.lower():
                m = re.search(
                    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                    line, re.I
                )
                if m:
                    subgroup_guid = m.group(1).lower()
                    in_wireless = True
            elif in_wireless and "power saving mode" in line.lower():
                m = re.search(
                    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                    line, re.I
                )
                if m and subgroup_guid:
                    return subgroup_guid, m.group(1).lower()
    except Exception:
        pass
    return None


# Cache the wifi GUIDs at startup so we don't re-query every sync
_WIFI_GUIDS = None

def get_wifi_guids_cached():
    global _WIFI_GUIDS
    if _WIFI_GUIDS is None:
        _WIFI_GUIDS = get_wifi_power_save_guids()
    return _WIFI_GUIDS


def apply_settings(guid: str, settings: dict):
    """
    Push all settings from the profile dict to the Windows scheme.

    All values are written directly to the scheme via setacvalueindex /
    setdcvalueindex — no temporary scheme activation needed, no global
    side effects.

    Timeout values in the JSON are in minutes; Windows expects seconds
    for setacvalueindex (multiply by 60). 0 minutes = 0 seconds = never.

    The 'hibernate' key is intentionally NOT applied here because
    powercfg /hibernate is a global system switch, not per-scheme.
    It is applied in cmd_apply() only when a profile is actually activated,
    so the active profile's preference wins at switch time.
    """
    s = settings

    # ── Timeout subgroup GUIDs ────────────────────────────────────────────────
    # Subgroup: sleep settings  238c9fa8-0aad-41ed-83f4-97be242c8f20
    # Subgroup: display         7516b95f-f776-4464-8c53-06167f40cc99
    TIMEOUT_SETTINGS = {
        "monitor_timeout_ac":   ("7516b95f-f776-4464-8c53-06167f40cc99",
                                  "3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e", "AC"),
        "monitor_timeout_dc":   ("7516b95f-f776-4464-8c53-06167f40cc99",
                                  "3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e", "DC"),
        "standby_timeout_ac":   ("238c9fa8-0aad-41ed-83f4-97be242c8f20",
                                  "29f6c1db-86da-48c5-9fdb-f2b67b1f44da", "AC"),
        "standby_timeout_dc":   ("238c9fa8-0aad-41ed-83f4-97be242c8f20",
                                  "9d7815a6-7ee4-497e-8888-515a05f02364", "DC"),
        "hibernate_timeout_ac": ("238c9fa8-0aad-41ed-83f4-97be242c8f20",
                                  "9d7815a6-7ee4-497e-8888-515a05f02364", "AC"),
        "hibernate_timeout_dc": ("238c9fa8-0aad-41ed-83f4-97be242c8f20",
                                  "bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d", "DC"),
    }

    # Write timeout settings directly to scheme (minutes → seconds)
    for key, (subgroup, setting, ac_dc) in TIMEOUT_SETTINGS.items():
        if key not in s:
            continue
        val = str(int(s[key]) * 60)   # minutes to seconds; 0 stays 0
        cmd = "/setacvalueindex" if ac_dc == "AC" else "/setdcvalueindex"
        run(["powercfg", cmd, guid, subgroup, setting, val])

    # ── CPU, USB, WiFi, lid/button settings ──────────────────────────────────
    for key, (subgroup, setting_guid, index_type) in POWERCFG_SETTINGS.items():
        if key == "hibernate":   # handled at activation time, not sync time
            continue
        if key not in s:
            continue

        if key == "wifi_power_save":
            wifi = get_wifi_guids_cached()
            if wifi is None:
                print("  [info] WiFi power save not available on this machine — skipping.")
                continue
            subgroup, setting_guid = wifi

        val = str(s[key])
        if index_type == "ACSettingIndex" or key.endswith("_ac"):
            run(["powercfg", "/setacvalueindex", guid, subgroup, setting_guid, val])
        elif index_type == "DCSettingIndex" or key.endswith("_dc"):
            run(["powercfg", "/setdcvalueindex", guid, subgroup, setting_guid, val])
        else:
            run(["powercfg", "/setacvalueindex", guid, subgroup, setting_guid, val])
            run(["powercfg", "/setdcvalueindex", guid, subgroup, setting_guid, val])


def sync_all_profiles(verbose=True):
    """Create/update all profiles from JSON."""
    data  = load_json(PROFILES_JSON)
    state = load_state()
    for profile in data["profiles"]:
        if verbose:
            print(f"  Syncing '{profile['name']}'…")
        guid = ensure_profile_exists(profile, state)
        apply_settings(guid, profile.get("settings", {}))
    if verbose:
        print("  Sync complete.")
    return data, state


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  CLI COMMANDS
# ╚══════════════════════════════════════════════════════════════════════════════

def cmd_list():
    data  = load_json(PROFILES_JSON)
    state = load_state()
    active = get_active_guid()
    print(f"\n{'Profile':<20} {'Status':<10} GUID")
    print("─" * 70)
    for p in data["profiles"]:
        name = p["name"]
        guid = state["guids"].get(name, "(not created yet)")
        marker = "● ACTIVE" if guid == active else ""
        print(f"  {name:<18} {marker:<10} {guid}")
    print()


def cmd_apply(name: str):
    data  = load_json(PROFILES_JSON)
    state = load_state()
    profiles = {p["name"].lower(): p for p in data["profiles"]}

    if name.lower() not in profiles:
        print(f"Profile '{name}' not found. Available: {', '.join(profiles)}")
        sys.exit(1)

    profile  = profiles[name.lower()]
    settings = profile.get("settings", {})
    guid     = ensure_profile_exists(profile, state)
    apply_settings(guid, settings)
    run(["powercfg", "/setactive", guid])

    # Apply hibernate at activation time only — it's a global system switch,
    # not per-scheme, so we set it to match the profile being activated.
    if "hibernate" in settings:
        run(["powercfg", "/hibernate", "on" if settings["hibernate"] else "off"])

    print(f"  ✓ Active profile → {profile['name']}")


def cmd_toggle():
    data   = load_json(PROFILES_JSON)
    state  = load_state()
    active = get_active_guid()
    names  = [p["name"] for p in data["profiles"]]
    guids  = [state["guids"].get(n, "") for n in names]

    try:
        idx = guids.index(active)
        next_idx = (idx + 1) % len(names)
    except ValueError:
        next_idx = 0

    cmd_apply(names[next_idx])


def cmd_sync():
    sync_all_profiles(verbose=True)


def cmd_remove():
    """
    Clean uninstall:
      1. Switch active scheme back to Windows Balanced (the OS default)
      2. Delete every custom scheme we created (from state file)
      3. Delete the state file
    Windows built-in schemes (Balanced / High Performance / Power Saver)
    are never touched — only schemes this tool created are removed.
    """
    BALANCED_GUID = "381b4222-f694-41f0-9685-ff5bb260df2e"

    state = load_state()
    managed_guids = state.get("guids", {})   # {profile_name: guid}

    if not managed_guids:
        print("[power_manager] No managed profiles found in state file.")
        print("  Nothing to remove.")
        return

    # 1. Switch to Balanced first so we never delete the active scheme
    active = get_active_guid()
    if active in managed_guids.values():
        print("  Restoring default scheme -> Balanced...")
        rc, _, err = run(["powercfg", "/setactive", BALANCED_GUID])
        if rc == 0:
            print("  OK Active scheme restored to Balanced.")
        else:
            print(f"  [warn] Could not restore Balanced: {err.strip()}")
    else:
        print("  Active scheme is not a managed profile -- no restore needed.")

    # 2. Delete each managed scheme from Windows
    existing_schemes = get_windows_schemes()
    for name, guid in managed_guids.items():
        if guid.lower() in existing_schemes:
            rc, _, err = run(["powercfg", "/delete", guid])
            if rc == 0:
                print(f"  OK Deleted scheme '{name}' ({guid})")
            else:
                print(f"  [warn] Could not delete '{name}': {err.strip()}")
        else:
            print(f"  Scheme '{name}' ({guid}) not found in Windows -- already gone.")

    # 3. Remove the state file
    if STATE_FILE.exists():
        STATE_FILE.unlink()
        print(f"  OK Removed state file -> {STATE_FILE.name}")

    print("\n  Uninstall complete. Windows is back to its original power schemes.")
    print("  Tip: run  powercfg -restoredefaultschemes  to also reset built-in")
    print("       scheme settings to factory defaults (Balanced / HP / Power Saver).")


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  SHUTDOWN TIMER CONFIG  (reads/writes shutdown_timer.json)
# ╚══════════════════════════════════════════════════════════════════════════════

SHUTDOWN_CONFIG = SCRIPT_DIR / "shutdown_timer.json"

SHUTDOWN_DEFAULTS = {
    "enabled": True,
    "disable_mode": "none",
    "boot_session_id": "",
    "delay_hours": 1,
    "dialog_timeout_seconds": 60,
    "headless_grace_seconds": 600,
    "shutdown_warning_seconds": 60,
}

def load_shutdown_cfg() -> dict:
    if not SHUTDOWN_CONFIG.exists():
        return SHUTDOWN_DEFAULTS.copy()
    try:
        with open(SHUTDOWN_CONFIG, "r", encoding="utf-8") as f:
            raw = json.load(f)
        cfg = SHUTDOWN_DEFAULTS.copy()
        cfg.update({k: v for k, v in raw.items() if not k.startswith("_")})
        return cfg
    except Exception:
        return SHUTDOWN_DEFAULTS.copy()


def save_shutdown_cfg(cfg: dict):
    out = {
        "_comment": "Shutdown timer config. Changes are picked up on the next cycle automatically.",
        "_help": {
            "enabled": "true/false — master switch",
            "disable_mode": "none | permanent | this_boot",
            "boot_session_id": "auto-managed, do not edit",
            "delay_hours": "hours between shutdown checks",
            "dialog_timeout_seconds": "seconds the popup stays open",
            "headless_grace_seconds": "seconds to wait before shutdown when no user is logged in",
            "shutdown_warning_seconds": "OS shutdown /t grace period"
        }
    }
    out.update(cfg)
    with open(SHUTDOWN_CONFIG, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


def open_shutdown_config_window(on_save_cb=None):
    """Open a Tkinter settings window for the shutdown timer."""
    import tkinter as tk
    from tkinter import font as tkfont, messagebox

    cfg = load_shutdown_cfg()

    win = tk.Toplevel()
    win.title("Shutdown Timer — Configure")
    win.configure(bg="#1a1a1a")
    win.resizable(False, False)
    win.attributes("-topmost", True)

    try:
        title_font  = tkfont.Font(family="Segoe UI", size=13, weight="bold")
        label_font  = tkfont.Font(family="Segoe UI", size=10)
        input_font  = tkfont.Font(family="Consolas", size=10)
        btn_font    = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        small_font  = tkfont.Font(family="Segoe UI", size=8)
    except Exception:
        title_font = label_font = btn_font = small_font = tkfont.Font(size=10)
        input_font = tkfont.Font(size=10)

    # ── Header ────────────────────────────────────────────────────────────────
    tk.Label(win, text="Shutdown Timer Settings",
             font=title_font, bg="#1a1a1a", fg="#ffffff").pack(pady=(20, 4))
    tk.Frame(win, bg="#333333", height=1).pack(fill="x", padx=20, pady=(0, 10))

    fields_frame = tk.Frame(win, bg="#1a1a1a")
    fields_frame.pack(fill="x", padx=20)

    def make_row(parent, label, hint, row):
        tk.Label(parent, text=label, font=label_font,
                 bg="#1a1a1a", fg="#cccccc", anchor="w"
        ).grid(row=row, column=0, sticky="w", pady=4)
        tk.Label(parent, text=hint, font=small_font,
                 bg="#1a1a1a", fg="#666666", anchor="w"
        ).grid(row=row, column=2, sticky="w", padx=(8, 0))

    # ── Disable mode radio buttons ────────────────────────────────────────────
    tk.Label(fields_frame, text="Timer status", font=label_font,
             bg="#1a1a1a", fg="#cccccc", anchor="w"
    ).grid(row=0, column=0, sticky="w", pady=(4, 0))

    mode_var = tk.StringVar(value=cfg.get("disable_mode", "none"))

    radio_frame = tk.Frame(fields_frame, bg="#1a1a1a")
    radio_frame.grid(row=0, column=1, columnspan=2, sticky="w", padx=(12, 0))

    MODES = [
        ("none",       "Enabled",                   "#4CAF50"),
        ("this_boot",  "Disable until next boot",   "#FF9800"),
        ("permanent",  "Disable permanently",        "#f44336"),
    ]
    for val, label, color in MODES:
        tk.Radiobutton(
            radio_frame, text=label, variable=mode_var, value=val,
            bg="#1a1a1a", fg=color, selectcolor="#2a2a2a",
            activebackground="#1a1a1a", activeforeground=color,
            font=label_font
        ).pack(side="left", padx=(0, 12))

    tk.Frame(fields_frame, bg="#333333", height=1).grid(
        row=1, column=0, columnspan=3, sticky="ew", pady=(8, 4))

    # ── Numeric fields ────────────────────────────────────────────────────────
    ROWS = [
        ("delay_hours",              "Interval (hours)",           "Hours between shutdown checks"),
        ("dialog_timeout_seconds",   "Prompt timeout (seconds)",   "How long the popup waits for input"),
        ("headless_grace_seconds",   "Headless grace (seconds)",   "Delay before shutdown when no user logged in"),
        ("shutdown_warning_seconds", "Shutdown warning (seconds)", "OS grace period after shutdown is issued"),
    ]

    entries = {}
    for i, (key, label, hint) in enumerate(ROWS, start=2):
        make_row(fields_frame, label, hint, i)
        var = tk.StringVar(value=str(cfg[key]))
        e = tk.Entry(fields_frame, textvariable=var, font=input_font,
                     bg="#2a2a2a", fg="#ffffff", insertbackground="#ffffff",
                     relief="flat", width=8)
        e.grid(row=i, column=1, sticky="w", padx=(12, 0), pady=4)
        entries[key] = var

    fields_frame.columnconfigure(2, weight=1)

    tk.Frame(win, bg="#333333", height=1).pack(fill="x", padx=20, pady=(12, 0))

    # ── Buttons ───────────────────────────────────────────────────────────────
    btn_frame = tk.Frame(win, bg="#1a1a1a")
    btn_frame.pack(pady=16)

    def on_save():
        try:
            new_cfg = {
                "enabled": mode_var.get() == "none",
                "disable_mode": mode_var.get(),
                "boot_session_id": cfg.get("boot_session_id", ""),
                "delay_hours":              float(entries["delay_hours"].get()),
                "dialog_timeout_seconds":   int(entries["dialog_timeout_seconds"].get()),
                "headless_grace_seconds":   int(entries["headless_grace_seconds"].get()),
                "shutdown_warning_seconds": int(entries["shutdown_warning_seconds"].get()),
            }
        except ValueError:
            messagebox.showerror("Invalid input",
                                 "Please enter valid numbers for all fields.",
                                 parent=win)
            return
        save_shutdown_cfg(new_cfg)
        if on_save_cb:
            on_save_cb(new_cfg)
        win.destroy()

    tk.Button(btn_frame, text="Save", font=btn_font,
              bg="#1a7f3c", fg="#ffffff", activebackground="#25a352",
              relief="flat", padx=20, pady=8, cursor="hand2",
              command=on_save
    ).pack(side="left", padx=8)

    tk.Button(btn_frame, text="Cancel", font=btn_font,
              bg="#3a3a3a", fg="#ffffff", activebackground="#555555",
              relief="flat", padx=20, pady=8, cursor="hand2",
              command=win.destroy
    ).pack(side="left", padx=8)

    # Center on screen
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  TRAY STARTUP INSTALL / REMOVE
# ╚══════════════════════════════════════════════════════════════════════════════

TRAY_REG_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
TRAY_REG_NAME = "PowerManagerTray"
SHUTDOWN_TASK = "ShutdownTimer"


def tray_install():
    """Register the tray to launch at Windows login (no admin needed)."""
    try:
        import winreg
        cmd = f'"{PYTHON_EXE}" "{SCRIPT_PATH}"'
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, TRAY_REG_KEY, 0,
                             winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, TRAY_REG_NAME, 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
        print(f"[power_manager] Tray registered at login -> {cmd}")
    except Exception as e:
        print(f"[power_manager] Tray install failed: {e}")


def tray_remove():
    """Remove the tray from Windows login startup."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, TRAY_REG_KEY, 0,
                             winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, TRAY_REG_NAME)
        winreg.CloseKey(key)
        print("[power_manager] Tray removed from login startup.")
    except FileNotFoundError:
        print("[power_manager] Tray was not registered — nothing to remove.")
    except Exception as e:
        print(f"[power_manager] Tray remove failed: {e}")


def tray_is_installed() -> bool:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, TRAY_REG_KEY, 0,
                             winreg.KEY_READ)
        winreg.QueryValueEx(key, TRAY_REG_NAME)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def tray_enable():
    """Re-enable tray at login (same as install but only re-adds registry key)."""
    if tray_is_installed():
        print("[power_manager] Tray is already enabled at login.")
        return
    tray_install()
    print("[power_manager] Tray enabled — will start at next login.")


def tray_disable():
    """Disable tray at login without removing power schemes."""
    if not tray_is_installed():
        print("[power_manager] Tray is not registered at login — nothing to disable.")
        return
    tray_remove()
    print("[power_manager] Tray disabled — will no longer start at login.")
    print("  Power schemes remain. Re-enable with: python power_manager.py enable")


def tray_status():
    """Print full status of tray and shutdown timer."""
    installed  = tray_is_installed()
    task       = shutdown_task_is_installed()
    active     = get_active_guid()
    schemes    = get_windows_schemes()
    state      = load_state()
    scfg       = load_shutdown_cfg()
    mode       = scfg.get("disable_mode", "none")

    MODE_LABELS = {
        "none":      "Enabled",
        "this_boot": "Disabled until next boot",
        "permanent": "Disabled permanently",
    }

    print()
    print("=" * 52)
    print("  Power Manager — Status")
    print("=" * 52)

    print(f'\n  Tray at login : {"YES" if installed else "NO"}')

    print("\n  Power profiles:")
    for name, guid in state.get("guids", {}).items():
        marker = " (ACTIVE)" if guid.lower() == active else ""
        label  = schemes.get(guid.lower(), guid)
        print(f"    {name:<20} {guid}  {marker}")

    print("\n  Shutdown Timer:")
    print(f"    Scheduled Task : {'Installed' if task else 'Not installed'}")
    if task:
        print(f"    State          : {MODE_LABELS.get(mode, mode)}")
        print(f"    Interval       : {scfg['delay_hours']}h")
        print(f"    Prompt timeout : {scfg['dialog_timeout_seconds']}s")
        print(f"    Headless grace : {scfg['headless_grace_seconds']}s")
        print(f"    Shutdown warn  : {scfg['shutdown_warning_seconds']}s")

    print()


def shutdown_task_is_installed() -> bool:
    """Check if the ShutdownTimer scheduled task exists in Windows."""
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", SHUTDOWN_TASK],
            capture_output=True, text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def shutdown_task_install():
    """Install the shutdown timer scheduled task by calling shutdown_timer.py install."""
    timer_script = SCRIPT_DIR / "shutdown_timer.py"
    if not timer_script.exists():
        print(f"[power_manager] shutdown_timer.py not found at {timer_script}")
        return False
    result = subprocess.run(
        [PYTHON_EXE, str(timer_script), "install"],
        capture_output=True, text=True
    )
    print(result.stdout)
    return result.returncode == 0


def shutdown_task_remove():
    """Remove the shutdown timer scheduled task by calling shutdown_timer.py remove."""
    timer_script = SCRIPT_DIR / "shutdown_timer.py"
    if not timer_script.exists():
        print(f"[power_manager] shutdown_timer.py not found at {timer_script}")
        return False
    result = subprocess.run(
        [PYTHON_EXE, str(timer_script), "remove"],
        capture_output=True, text=True
    )
    print(result.stdout)
    return result.returncode == 0


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  SYSTEM TRAY GUI
# ╚══════════════════════════════════════════════════════════════════════════════

def launch_tray():
    try:
        import pystray
        from PIL import Image, ImageDraw
        import tkinter as tk
    except ImportError:
        print("pystray / Pillow not installed.")
        print("Run:  pip install pystray pillow")
        sys.exit(1)

    data, state = sync_all_profiles(verbose=False)

    # Hidden Tk root needed to host config windows
    tk_root = tk.Tk()
    tk_root.withdraw()

    def make_icon(color="#4CAF50"):
        img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d    = ImageDraw.Draw(img)
        bolt = [(38, 4), (20, 34), (32, 34), (26, 60), (46, 28), (34, 28)]
        d.polygon(bolt, fill=color)
        return img

    PROFILE_COLORS = {0: "#4CAF50", 1: "#2196F3", 2: "#FF9800"}

    def get_profile_index():
        active = get_active_guid()
        guids  = [state["guids"].get(p["name"], "") for p in data["profiles"]]
        try:
            return guids.index(active)
        except ValueError:
            return -1

    def shutdown_status_label():
        cfg = load_shutdown_cfg()
        if not cfg["enabled"]:
            return "Shutdown Timer: Disabled"
        return f"Shutdown Timer: every {cfg['delay_hours']}h"

    def build_menu(icon):
        active = get_active_guid()

        # ── Power profile items ───────────────────────────────────────────────
        profile_items = []
        for p in data["profiles"]:
            name  = p["name"]
            guid  = state["guids"].get(name, "")
            label = f"✓  {name}" if guid == active else f"    {name}"
            def make_cb(n=name):
                def cb(icon, item):
                    cmd_apply(n)
                    idx = get_profile_index()
                    icon.icon = make_icon(PROFILE_COLORS.get(idx, "#9E9E9E"))
                    icon.menu = build_menu(icon)
                    icon.update_menu()
                return cb
            profile_items.append(pystray.MenuItem(label, make_cb()))

        def open_config(icon, item):
            def after_save(new_cfg):
                icon.menu = build_menu(icon)
                icon.update_menu()
            tk_root.after(0, lambda: open_shutdown_config_window(on_save_cb=after_save))

        def set_mode(new_mode):
            def cb(icon, item):
                c = load_shutdown_cfg()
                c["disable_mode"] = new_mode
                c["enabled"] = (new_mode == "none")
                save_shutdown_cfg(c)
                icon.menu = build_menu(icon)
                icon.update_menu()
            return cb

        # ── Shutdown timer installed state ───────────────────────────────────
        task_installed = shutdown_task_is_installed()

        if not task_installed:
            # Timer not installed — show install option only
            shutdown_items = [
                pystray.MenuItem(
                    "✗  Shutdown Timer  —  Not installed",
                    pystray.Menu(
                        pystray.MenuItem("Install shutdown timer", lambda icon, item: (
                            shutdown_task_install(),
                            icon.menu.__class__,   # force menu rebuild
                            setattr(icon, "menu", build_menu(icon)),
                            icon.update_menu()
                        )),
                    )
                ),
            ]
        else:
            # Timer installed — build label from actual JSON state
            cfg = load_shutdown_cfg()
            mode = cfg.get("disable_mode", "none")
            MODE_ICONS = {"none": "✓", "this_boot": "~", "permanent": "✗"}
            MODE_LABELS = {
                "none":       "Enabled",
                "this_boot":  "Disabled (this boot)",
                "permanent":  "Disabled (permanent)",
            }
            timer_label = (
                f"{MODE_ICONS.get(mode, '?')}  Shutdown Timer"
                f"  —  {MODE_LABELS.get(mode, mode)}"
                f"  ({cfg['delay_hours']}h)"
            )

            def do_uninstall(icon, item):
                shutdown_task_remove()
                icon.menu = build_menu(icon)
                icon.update_menu()

            shutdown_items = [
                pystray.MenuItem(timer_label, pystray.Menu(
                    pystray.MenuItem("Enable",                    set_mode("none")),
                    pystray.MenuItem("Disable until next boot",  set_mode("this_boot")),
                    pystray.MenuItem("Disable permanently",      set_mode("permanent")),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Configure...",             open_config),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Uninstall shutdown timer", do_uninstall),
                )),
            ]

        # ── Start at login toggle ────────────────────────────────────────────
        at_login = tray_is_installed()
        login_label = f"{'✓' if at_login else '   '}  Start tray at login"

        def toggle_login(icon, item):
            if tray_is_installed():
                tray_disable()
            else:
                tray_enable()
            icon.menu = build_menu(icon)
            icon.update_menu()

        return pystray.Menu(
            pystray.MenuItem("⚡  Power Profile", pystray.Menu(*profile_items)),
            pystray.MenuItem("Toggle profile (cycle)", lambda icon, item: (
                cmd_toggle(),
                setattr(icon, "icon", make_icon(
                    PROFILE_COLORS.get(get_profile_index(), "#9E9E9E")
                )),
                icon.update_menu()
            )),
            pystray.Menu.SEPARATOR,
            *shutdown_items,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(login_label,              toggle_login),
            pystray.MenuItem("Reload profiles from JSON", lambda icon, item: reload_profiles(icon)),
            pystray.MenuItem("Exit", lambda icon, item: (tk_root.quit(), icon.stop())),
        )

    def reload_profiles(icon):
        nonlocal data, state
        data, state = sync_all_profiles(verbose=False)
        idx = get_profile_index()
        icon.icon = make_icon(PROFILE_COLORS.get(idx, "#9E9E9E"))
        icon.menu = build_menu(icon)
        icon.update_menu()
        print("[power_manager] Profiles reloaded.")

    idx  = get_profile_index()
    icon = pystray.Icon(
        "PowerManager",
        make_icon(PROFILE_COLORS.get(idx, "#9E9E9E")),
        "Power Manager",
    )
    icon.menu = build_menu(icon)

    # Run pystray in a thread; Tk mainloop on main thread (needed for windows)
    t = threading.Thread(target=icon.run, daemon=True)
    t.start()

    print("[power_manager] Tray icon running. Right-click to manage.")
    tk_root.mainloop()


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  ENTRY POINT
# ╚══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if not is_admin():
        print("[power_manager] WARNING: Not running as Administrator.")
        print("  Some settings (hibernate, CPU throttle) may not apply.")
        print("  Re-run as Administrator for full control.\n")

    args = sys.argv[1:]

    if not args:
        launch_tray()
    elif args[0] == "list":
        cmd_list()
    elif args[0] == "apply" and len(args) > 1:
        cmd_apply(args[1])
    elif args[0] == "toggle":
        cmd_toggle()
    elif args[0] == "sync":
        cmd_sync()
    elif args[0] == "remove":
        cmd_remove()
    elif args[0] == "install":
        # Register tray at login + sync profiles
        tray_install()
        sync_all_profiles(verbose=True)
        print("[power_manager] Done. Tray will start automatically at next login.")
        print("  Launch now: pythonw power_manager.py")
    elif args[0] == "uninstall":
        # Remove tray from login + restore default power scheme
        tray_remove()
        cmd_remove()
        print("[power_manager] Tray uninstalled and power schemes restored.")
    elif args[0] == "enable":
        tray_enable()
    elif args[0] == "disable":
        tray_disable()
    elif args[0] == "status":
        tray_status()
    else:
        print(__doc__)
