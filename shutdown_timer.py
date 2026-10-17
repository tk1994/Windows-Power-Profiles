"""
shutdown_timer.py — Cross-Platform Shutdown Timer
==================================================
Config is read from shutdown_timer.json (same directory).
Every N hours it checks for an active user session:
  • User logged in  → show a GUI prompt (configurable seconds to delay)
  • No user         → wait headless_grace_seconds, then shut down

Usage
-----
  python shutdown_timer.py install   # install & start as a background service
  python shutdown_timer.py remove    # stop & uninstall the service
  python shutdown_timer.py run       # run directly (foreground, for testing)

Config is live-reloaded every cycle from shutdown_timer.json —
no restart needed after editing the file.
"""

import os
import sys
import json
import platform
import subprocess
import time
import threading
import textwrap
import getpass
from pathlib import Path

SCRIPT_DIR   = Path(__file__).parent
CONFIG_FILE  = SCRIPT_DIR / "shutdown_timer.json"
LOG_FILE     = SCRIPT_DIR / "shutdown_timer.log"
SERVICE_NAME = "ShutdownTimer"
SCRIPT_PATH  = os.path.abspath(__file__)
OS           = platform.system()


def _resolve_real_python() -> str:
    """
    Return the real python.exe path, rejecting Microsoft Store alias stubs.
    Store aliases live under WindowsApps and cannot be accessed by SYSTEM.
    """
    candidate = sys.executable

    # If it's a Store alias, search for the real install
    if "WindowsApps" in candidate or "Microsoft\\WindowsApps" in candidate:
        # Try py launcher first (most reliable)
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

        # Search common install locations
        search_paths = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python",
            Path("C:/Python313"),
            Path("C:/Python312"),
            Path("C:/Python311"),
            Path("C:/Python310"),
            Path("C:/Program Files/Python313"),
            Path("C:/Program Files/Python312"),
            Path("C:/Program Files/Python311"),
        ]
        for base in search_paths:
            for pattern in ["python.exe", "*/python.exe"]:
                found = list(base.glob(pattern))
                for f in found:
                    if f.is_file() and "WindowsApps" not in str(f):
                        return str(f)

        raise RuntimeError(
            "Could not find a real Python installation.\n"
            "The Microsoft Store Python alias cannot be used by the Scheduled Task.\n"
            "Please install Python from https://python.org and re-run install."
        )

    return candidate


PYTHON_EXE = _resolve_real_python() if OS == "Windows" else sys.executable


def log(msg: str):
    """Write timestamped message to both stdout and log file."""
    import datetime
    line = f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # never crash because of logging

DEFAULT_CONFIG = {
    "enabled": True,
    "disable_mode": "none",
    "boot_session_id": "",
    "delay_hours": 1,
    "dialog_timeout_seconds": 60,
    "headless_grace_seconds": 600,
    "shutdown_warning_seconds": 60,
}

# ╔══════════════════════════════════════════════════════════════════════════════
# ║  CONFIG
# ╚══════════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    """Load config from JSON, falling back to defaults for missing keys."""
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = DEFAULT_CONFIG.copy()
        cfg.update({k: v for k, v in data.items() if not k.startswith("_")})
        return cfg
    except Exception as e:
        print(f"[shutdown_timer] Failed to load config: {e} — using defaults.", flush=True)
        return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    """Write config back to JSON, preserving comment keys."""
    out = {
        "_comment": "Shutdown timer config. Changes are picked up on the next cycle automatically.",
        "_help": {
            "enabled": "true/false — master on/off switch",
            "disable_mode": "none | permanent | this_boot",
            "boot_session_id": "auto-managed — do not edit",
            "delay_hours": "hours between shutdown checks",
            "dialog_timeout_seconds": "seconds the popup stays open before auto-dismissing",
            "headless_grace_seconds": "seconds to wait before shutdown when no user is logged in",
            "shutdown_warning_seconds": "OS shutdown /t grace period after command is issued"
        }
    }
    out.update(cfg)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  SESSION DETECTION
# ╚══════════════════════════════════════════════════════════════════════════════

def is_user_logged_in() -> bool:
    try:
        if OS == "Windows":
            out = subprocess.check_output(
                ["query", "session"], stderr=subprocess.DEVNULL, text=True
            )
            log(f"[session] query session output:\n{out.strip()}")
            for line in out.splitlines():
                if "Active" in line or "Disc" in line:
                    log("[session] Active user session detected.")
                    return True
            log("[session] No active user session found.")
            return False
        else:
            out = subprocess.check_output(["who"], text=True)
            logged_in = bool(out.strip())
            log(f"[session] who output: {out.strip()!r} -> logged_in={logged_in}")
            return logged_in
    except Exception as e:
        log(f"[session] ERROR checking session: {e} — assuming logged in.")
        return True


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  SHUTDOWN
# ╚══════════════════════════════════════════════════════════════════════════════

def do_shutdown(warning_seconds: int):
    log(f"[shutdown] Issuing shutdown command. Warning: {warning_seconds}s.")
    try:
        if OS == "Windows":
            result = subprocess.run(
                ["shutdown", "/s", "/f", "/t", str(warning_seconds),
                 "/c", "Scheduled shutdown by ShutdownTimer."],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                log(f"[shutdown] ERROR: {result.stderr.strip()}")
            else:
                log("[shutdown] Shutdown command accepted by Windows.")
        else:
            subprocess.run(
                ["sudo", "shutdown", "-h", f"+{warning_seconds // 60 or 1}"],
                check=False
            )
    except Exception as e:
        log(f"[shutdown] EXCEPTION: {e}")


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  UI PROMPT
# ╚══════════════════════════════════════════════════════════════════════════════

def run_ui_prompt(dialog_timeout: int) -> bool:
    """Show delay prompt. Returns True if user delayed, False otherwise."""
    log(f"[prompt] Attempting to show UI prompt. Timeout={dialog_timeout}s.")
    try:
        import tkinter as tk
        from tkinter import font as tkfont

        result = {"delayed": False}
        root = tk.Tk()
        root.withdraw()
        root.update()  # throws if no display
        log("[prompt] Tkinter display available — showing window.")

        dlg = tk.Toplevel(root)
        dlg.title("Scheduled Shutdown Warning")
        dlg.configure(bg="#0d0d0d")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)

        def respond(choice):
            result["delayed"] = (choice == "delay")
            root.quit()

        dlg.protocol("WM_DELETE_WINDOW", lambda: respond("shutdown"))

        try:
            h1   = tkfont.Font(family="Segoe UI", size=22, weight="bold")
            body = tkfont.Font(family="Segoe UI", size=11)
            tmr  = tkfont.Font(family="Consolas",  size=36, weight="bold")
            btn  = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        except Exception:
            h1   = tkfont.Font(size=20, weight="bold")
            body = tkfont.Font(size=10)
            tmr  = tkfont.Font(size=28, weight="bold")
            btn  = tkfont.Font(size=10, weight="bold")

        frame = tk.Frame(dlg, bg="#0d0d0d", padx=48, pady=36)
        frame.pack()

        tk.Label(frame, text="\u23fb", font=tkfont.Font(size=48),
                 bg="#0d0d0d", fg="#ff4444").pack()
        tk.Label(frame, text="Scheduled Shutdown",
                 font=h1, bg="#0d0d0d", fg="#ffffff").pack(pady=(6, 0))
        tk.Label(frame,
                 text=(
                     "Your computer is scheduled to shut down.\n"
                     f"Click  Delay 1 Hour  within {dialog_timeout} seconds to postpone."
                 ),
                 font=body, bg="#0d0d0d", fg="#aaaaaa", justify="center"
        ).pack(pady=(12, 4))

        cvar = tk.StringVar(value=str(dialog_timeout))
        tk.Label(frame, textvariable=cvar,
                 font=tmr, bg="#0d0d0d", fg="#ff4444").pack(pady=(4, 20))

        bf = tk.Frame(frame, bg="#0d0d0d")
        bf.pack()
        tk.Button(bf, text="  Delay 1 Hour",
                  font=btn, bg="#1a7f3c", fg="#ffffff",
                  activebackground="#25a352", relief="flat",
                  padx=24, pady=10, cursor="hand2",
                  command=lambda: respond("delay")
        ).grid(row=0, column=0, padx=10)
        tk.Button(bf, text="  Shutdown Now",
                  font=btn, bg="#7f1a1a", fg="#ffffff",
                  activebackground="#a32525", relief="flat",
                  padx=24, pady=10, cursor="hand2",
                  command=lambda: respond("shutdown")
        ).grid(row=0, column=1, padx=10)

        dlg.update_idletasks()
        w, h = dlg.winfo_width(), dlg.winfo_height()
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        dlg.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        remaining = [dialog_timeout]

        def tick():
            if result["delayed"]:
                return
            remaining[0] -= 1
            cvar.set(str(max(remaining[0], 0)))
            if remaining[0] <= 0:
                respond("shutdown")
            else:
                root.after(1000, tick)

        root.after(1000, tick)
        root.mainloop()
        try:
            root.destroy()
        except Exception:
            pass

        return result["delayed"]

    except Exception as e:
        log(f"[prompt] GUI FAILED: {e}")
        log("[prompt] Falling back to terminal prompt.")

    # Terminal fallback
    try:
        import select
        print(
            f"\n[shutdown_timer] SHUTDOWN in {dialog_timeout}s. "
            "Type 'delay' and press Enter to postpone: ",
            end="", flush=True
        )
        ready, _, _ = select.select([sys.stdin], [], [], dialog_timeout)
        if ready:
            return sys.stdin.readline().strip().lower() == "delay"
        return False
    except Exception:
        return False


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  TIMER LOOP  (re-reads config every cycle)
# ╚══════════════════════════════════════════════════════════════════════════════

import uuid as _uuid
_THIS_BOOT_SESSION = str(_uuid.uuid4())


def _is_disabled(cfg: dict) -> tuple[bool, str]:
    """
    Return (disabled, reason).
    disable_mode values:
      "none"       — not disabled, timer runs normally
      "permanent"  — disabled until user re-enables via tray/JSON
      "this_boot"  — disabled for this boot only; clears on next boot
    """
    mode = cfg.get("disable_mode", "none")
    if mode == "permanent":
        return True, "permanently disabled"
    if mode == "this_boot":
        # Compare boot session IDs — if they match, still same boot
        if cfg.get("boot_session_id", "") == _THIS_BOOT_SESSION:
            return True, "disabled for this boot"
        else:
            # New boot detected — clear the this_boot disable
            return False, "this_boot disable expired (new boot)"
    return False, ""


def _stamp_boot_session(cfg: dict):
    """Write our boot session ID into the config so this_boot disable works."""
    cfg["boot_session_id"] = _THIS_BOOT_SESSION
    save_config(cfg)


def timer_loop(skip_first_sleep: bool = False):
    log(f"[startup] shutdown_timer started. PID={os.getpid()} OS={OS}")
    log(f"[startup] Script : {SCRIPT_PATH}")
    log(f"[startup] Python : {PYTHON_EXE}")
    log(f"[startup] Config : {CONFIG_FILE}")
    log(f"[startup] Log    : {LOG_FILE}")

    # On every fresh start, stamp our boot session ID into config.
    cfg = load_config()
    log(f"[startup] Config loaded: {cfg}")
    if cfg.get("disable_mode") == "this_boot":
        if cfg.get("boot_session_id", "") != _THIS_BOOT_SESSION:
            log("[startup] New boot detected — clearing this_boot disable.")
            cfg["disable_mode"] = "none"
            cfg["boot_session_id"] = _THIS_BOOT_SESSION
            save_config(cfg)
        else:
            _stamp_boot_session(cfg)
    else:
        _stamp_boot_session(cfg)

    while True:
        cfg = load_config()
        disabled, reason = _is_disabled(cfg)

        if disabled:
            log(f"[timer] {reason} — sleeping 60s before recheck.")
            time.sleep(60)
            continue

        if skip_first_sleep:
            log("[timer] Skipping initial sleep (test mode).")
            skip_first_sleep = False
        else:
            delay_secs = cfg["delay_hours"] * 3600
            log(f"[timer] Sleeping {cfg['delay_hours']}h ({delay_secs:.0f}s)…")
            time.sleep(delay_secs)

        # Re-read config after sleep
        cfg = load_config()
        disabled, reason = _is_disabled(cfg)
        if disabled:
            log(f"[timer] {reason} — skipping this cycle.")
            continue

        log("[timer] Cycle triggered — checking for user session.")
        if is_user_logged_in():
            log("[timer] User logged in — showing UI prompt.")
            try:
                delayed = run_ui_prompt(cfg["dialog_timeout_seconds"])
            except Exception as e:
                log(f"[timer] ERROR in UI prompt: {e}")
                delayed = False
            if delayed:
                log("[timer] User delayed. Resetting timer.")
                continue
            log("[timer] Not delayed — proceeding to shutdown.")
        else:
            grace = cfg["headless_grace_seconds"]
            log(f"[timer] No user logged in — shutting down in {grace}s.")
            time.sleep(grace)

        do_shutdown(cfg["shutdown_warning_seconds"])
        break


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  INSTALL / REMOVE
# ╚══════════════════════════════════════════════════════════════════════════════

def windows_install():
    xml = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-16"?>
        <Task version="1.2"
              xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
          <RegistrationInfo>
            <Description>Hourly shutdown timer with delay prompt.</Description>
          </RegistrationInfo>
          <Triggers>
            <BootTrigger><Enabled>true</Enabled></BootTrigger>
          </Triggers>
          <Principals>
            <Principal id="Author">
              <UserId>S-1-5-18</UserId>
              <RunLevel>HighestAvailable</RunLevel>
            </Principal>
          </Principals>
          <Settings>
            <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
            <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
            <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
            <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
            <Enabled>true</Enabled>
          </Settings>
          <Actions>
            <Exec>
              <Command>{PYTHON_EXE}</Command>
              <Arguments>"{SCRIPT_PATH}" run</Arguments>
            </Exec>
          </Actions>
        </Task>
    """)
    xml_path = os.path.join(os.environ.get("TEMP", "."), "shutdown_timer_task.xml")
    with open(xml_path, "w", encoding="utf-16") as f:
        f.write(xml)
    subprocess.run(["schtasks", "/delete", "/tn", SERVICE_NAME, "/f"],
                   stderr=subprocess.DEVNULL)
    result = subprocess.run(
        ["schtasks", "/create", "/tn", SERVICE_NAME, "/xml", xml_path, "/f"],
        capture_output=True, text=True
    )
    os.remove(xml_path)
    if result.returncode == 0:
        # Explicitly enable in case a same-named disabled task existed before
        subprocess.run(
            ["schtasks", "/change", "/tn", SERVICE_NAME, "/enable"],
            capture_output=True
        )
        print(f"[shutdown_timer] Installed as Scheduled Task '{SERVICE_NAME}'.")
        subprocess.Popen([PYTHON_EXE, SCRIPT_PATH, "run"],
                         creationflags=0x00000008, close_fds=True)
    else:
        print(f"[shutdown_timer] Install failed:\n{result.stderr}")
        print("Tip: run as Administrator.")


def windows_remove():
    subprocess.run(["schtasks", "/delete", "/tn", SERVICE_NAME, "/f"],
                   stderr=subprocess.DEVNULL)
    subprocess.run(["taskkill", "/f", "/im", "python.exe", "/fi",
                    f"WINDOWTITLE eq {SERVICE_NAME}"],
                   stderr=subprocess.DEVNULL)
    print(f"[shutdown_timer] Removed task '{SERVICE_NAME}'.")


def macos_plist_path():
    return os.path.expanduser(
        f"~/Library/LaunchAgents/com.{getpass.getuser()}.shutdowntimer.plist"
    )

def macos_install():
    plist = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
            "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0"><dict>
            <key>Label</key><string>com.{getpass.getuser()}.shutdowntimer</string>
            <key>ProgramArguments</key>
            <array>
                <string>{PYTHON_EXE}</string>
                <string>{SCRIPT_PATH}</string>
                <string>run</string>
            </array>
            <key>RunAtLoad</key><true/>
            <key>KeepAlive</key><false/>
            <key>StandardOutPath</key><string>/tmp/shutdown_timer.log</string>
            <key>StandardErrorPath</key><string>/tmp/shutdown_timer.err</string>
        </dict></plist>
    """)
    path = macos_plist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(plist)
    subprocess.run(["launchctl", "unload", path], stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "load", path])
    print(f"[shutdown_timer] Installed launchd agent -> {path}")


def macos_remove():
    path = macos_plist_path()
    subprocess.run(["launchctl", "unload", path], stderr=subprocess.DEVNULL)
    try:
        os.remove(path)
        print(f"[shutdown_timer] Removed plist -> {path}")
    except FileNotFoundError:
        print("[shutdown_timer] No plist found.")


def linux_install():
    unit = textwrap.dedent(f"""\
        [Unit]
        Description=Hourly Shutdown Timer
        After=network.target
        [Service]
        Type=simple
        ExecStart={PYTHON_EXE} {SCRIPT_PATH} run
        Restart=no
        [Install]
        WantedBy=default.target
    """)
    path = Path("~/.config/systemd/user/shutdown-timer.service").expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit)
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    subprocess.run(["systemctl", "--user", "enable", "--now", "shutdown-timer.service"])
    print(f"[shutdown_timer] Installed systemd unit -> {path}")


def linux_remove():
    subprocess.run(["systemctl", "--user", "disable", "--now", "shutdown-timer.service"],
                   stderr=subprocess.DEVNULL)
    path = Path("~/.config/systemd/user/shutdown-timer.service").expanduser()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    print("[shutdown_timer] Removed systemd unit.")


def install():
    if OS == "Windows":
        windows_install()
    elif OS == "Darwin":
        macos_install()
    else:
        linux_install()


def remove():
    if OS == "Windows":
        windows_remove()
    elif OS == "Darwin":
        macos_remove()
    else:
        linux_remove()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "install":
        install()
    elif cmd == "remove":
        remove()
    elif cmd == "run":
        timer_loop()
    elif cmd == "test":
        # Skips the initial sleep — triggers the prompt immediately
        # Useful for verifying the UI and shutdown logic without waiting
        log("[startup] TEST MODE — skipping initial sleep.")
        timer_loop(skip_first_sleep=True)
    elif cmd == "log":
        # Print the current log file contents
        if LOG_FILE.exists():
            print(LOG_FILE.read_text(encoding="utf-8"))
        else:
            print(f"No log file found at {LOG_FILE}")
    else:
        print(__doc__)
        sys.exit(1)
