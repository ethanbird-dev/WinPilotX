# WinPilotX

**Hotkey-driven window manager for power users, gamers, and streamers.**

WinPilotX lets you instantly switch between any open window using global keyboard shortcuts — no alt-tabbing, no clicking around, no lost focus. Set up your windows once, save them as a named preset, and jump between them with a single keypress.

---

## Who It's For

- **Gamers** — switch between your game, Discord, OBS, and browser without breaking flow
- **Streamers** — flip between scenes, chat, alerts, and tools without touching the mouse
- **Power users** — manage complex multi-window workflows across one or multiple monitors

---

## Features

- **Global hotkeys** — Ctrl+Alt+1 through Ctrl+Alt+8 to instantly bring any window to the foreground
- **Named presets** — save a specific set of windows and restore them in one click
- **Multi-monitor support** — works across any display setup
- **Live window detection** — auto-detects open windows; dead windows are flagged, not crashed on simply click the refresh button anytime you need to verify what windows are running on your device.
- **Clean dark UI** — minimal, distraction-free interface built for efficiency
- **Reorderable slots** — drag your windows into the order that matches your hotkeys
- **No background bloat** — lightweight Python app, no Electron, no browser runtime

---

## Screenshots

> Coming soon

---

## Requirements

- Windows 10 or 11
- Python 3.11+ ([download](https://www.python.org/downloads/))
- `pywin32` library

---

## Installation

```bash
# 1. Clone the repo
git clone https://github.com/ethanbird-dev/WinPilotX.git
cd WinPilotX

# 2. Install dependencies
pip install pywin32

# 3. Run the app
run.bat
```

Or double-click `run.bat` directly — it launches without a console window.

---

## Usage

### My Windows tab

Shows your currently selected windows in hotkey order. Each row has:

- A status indicator (green = window alive, gray = closed)
- ↑ / ↓ buttons to reorder
- A hotkey chip showing which Ctrl+Alt+# is assigned
- A **Switch** button to bring that window to focus immediately
- An **×** button to remove it from your list

### Select Windows tab

Lists all open windows on your system. Click any row to add or remove it from your active list.

### Presets

Save your current window selection as a named preset (e.g. "Streaming Setup", "Gaming Mode"). Click a preset to instantly re-select any matching open windows.

### Hotkeys

| Shortcut   | Action                 |
| ---------- | ---------------------- |
| Ctrl+Alt+1 | Focus window in slot 1 |
| Ctrl+Alt+2 | Focus window in slot 2 |
| ...        | ...                    |
| Ctrl+Alt+8 | Focus window in slot 8 |

Hotkeys update automatically when you add, remove, or reorder windows.

---

## Built With

- Python 3.11
- Tkinter (GUI)
- pywin32 (Win32 API — window enumeration, focus, global hotkeys)

---

## License

MIT — free to use, modify, and distribute.

---

_Built by [Ethan Bird](https://github.com/ethanbird-dev)_
