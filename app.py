import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import win32gui
import win32con
import ctypes
import ctypes.wintypes
import threading
import queue
import json
import os

# ── Win32 hotkey constants ────────────────────────────────────────────────────

MOD_CONTROL = 0x0002
MOD_ALT     = 0x0001
MOD_SHIFT   = 0x0004
MOD_WIN     = 0x0008
WM_HOTKEY   = 0x0312
WM_QUIT     = 0x0012
user32      = ctypes.windll.user32
kernel32    = ctypes.windll.kernel32

# ── Config ────────────────────────────────────────────────────────────────────

_DIR         = os.path.dirname(os.path.abspath(__file__))
PRESETS_FILE = os.path.join(_DIR, "presets.json")
CONFIG_FILE  = os.path.join(_DIR, "config.json")
MAX_HOTKEYS  = 8

DEFAULT_HOTKEYS = [
    {"modifier": MOD_CONTROL | MOD_ALT, "key": str(i + 1)}
    for i in range(MAX_HOTKEYS)
]

# ── Color palette (GitHub Dark / Linear inspired) ─────────────────────────────

C_BG         = "#0d1117"
C_SURFACE    = "#161b22"
C_HOVER      = "#1c2230"
C_BORDER     = "#21262d"
C_ACCENT     = "#58a6ff"
C_ACCENT_BG  = "#1c2d3f"
C_ACCENT_HV  = "#243a52"
C_GREEN      = "#3fb950"
C_GRAY_DOT   = "#30363d"
C_RED        = "#f85149"
C_RED_HV     = "#3d1a1a"
C_AMBER      = "#e3b341"
C_AMBER_BG   = "#2b1f00"
C_TEXT       = "#e6edf3"
C_MUTED      = "#8b949e"
C_TITLE      = "#79c0ff"
C_NAV        = "#21262d"
C_NAV_HV     = "#30363d"
C_PRESET_BG  = "#1c2d3f"
C_PRESET_HV  = "#243a52"

# ── Win32 window helpers ──────────────────────────────────────────────────────

def get_all_windows():
    result = []
    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                result.append((hwnd, title))
    win32gui.EnumWindows(_cb, None)
    return result


def focus_window(hwnd):
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def is_window_alive(hwnd):
    return bool(hwnd) and win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd)


def _keysym_to_vk(keysym):
    """Convert a Tkinter keysym to a Windows virtual-key code, or None if unsupported."""
    if len(keysym) == 1:
        code = ord(keysym.upper())
        if 65 <= code <= 90 or 48 <= code <= 57:  # A–Z, 0–9
            return code
    if len(keysym) >= 2 and keysym[0] == "F" and keysym[1:].isdigit():
        n = int(keysym[1:])
        if 1 <= n <= 24:
            return 0x70 + (n - 1)  # VK_F1 = 0x70
    return None

# ── UI helpers ────────────────────────────────────────────────────────────────

def _setup_scrollbar_style():
    s = ttk.Style()
    s.theme_use("clam")
    s.configure("Dark.Vertical.TScrollbar",
        background  = C_BORDER,
        troughcolor = C_BG,
        bordercolor = C_BG,
        arrowcolor  = C_MUTED,
        arrowsize   = 10,
        relief      = tk.FLAT)
    s.map("Dark.Vertical.TScrollbar",
        background=[("active", C_HOVER), ("pressed", C_ACCENT_BG)])


def _hover(widget, normal_bg, hover_bg, normal_fg=None, hover_fg=None):
    """Attach Enter/Leave hover handlers to a widget."""
    def on_enter(e):
        widget.config(bg=hover_bg)
        if hover_fg:
            widget.config(fg=hover_fg)
    def on_leave(e):
        widget.config(bg=normal_bg)
        if normal_fg:
            widget.config(fg=normal_fg)
    widget.bind("<Enter>", on_enter)
    widget.bind("<Leave>", on_leave)


def _scrollable_area(parent, bg):
    canvas = tk.Canvas(parent, bg=bg, highlightthickness=0)
    sb     = ttk.Scrollbar(parent, orient="vertical",
                            command=canvas.yview, style="Dark.Vertical.TScrollbar")
    inner  = tk.Frame(canvas, bg=bg)

    inner.bind("<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=sb.set)
    canvas.bind("<Configure>",
        lambda e: canvas.itemconfig(win_id, width=e.width))

    return canvas, sb, inner, win_id

# ── App ───────────────────────────────────────────────────────────────────────

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("WinPilotX")
        self.root.geometry("720x700")
        self.root.configure(bg=C_BG)
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        _logo_path = os.path.join(_DIR, "winpilotxlogo.png")
        try:
            self._logo_img = tk.PhotoImage(file=_logo_path)
            self.root.iconphoto(True, self._logo_img)
            self._logo_small = self._logo_img.subsample(6)
        except Exception:
            self._logo_img = None
            self._logo_small = None

        _setup_scrollbar_style()

        self.presets  = {}
        self.selected = []
        self.all_wins = []

        # Hotkey thread state
        self._sw_queue     = queue.Queue()
        self._hk_thread    = None
        self._hk_thread_id = None
        self._hk_id_ready  = threading.Event()

        self._load_presets()
        self._load_config()
        self._build_ui()
        self.refresh()
        self._poll_switch_queue()

    # ── Hotkeys ───────────────────────────────────────────────────────────────

    def _register_hotkeys(self):
        self._stop_hotkey_thread()
        if any(w["hwnd"] for w in self.selected[:MAX_HOTKEYS]):
            self._hk_id_ready.clear()
            self._hk_thread = threading.Thread(
                target=self._hotkey_loop,
                args=(list(self.selected), list(self.hotkey_config)),
                daemon=True)
            self._hk_thread.start()

    def _stop_hotkey_thread(self):
        if self._hk_thread and self._hk_thread.is_alive():
            if self._hk_id_ready.wait(timeout=1.0) and self._hk_thread_id:
                user32.PostThreadMessageW(self._hk_thread_id, WM_QUIT, 0, 0)
            self._hk_thread.join(timeout=1.0)
        self._hk_thread    = None
        self._hk_thread_id = None

    def _hotkey_loop(self, windows, hotkey_cfg):
        self._hk_thread_id = kernel32.GetCurrentThreadId()
        self._hk_id_ready.set()
        registered = []
        for i, win in enumerate(windows[:MAX_HOTKEYS]):
            if not win["hwnd"]:
                continue
            cfg = hotkey_cfg[i] if i < len(hotkey_cfg) else None
            if not cfg or not cfg.get("key") or cfg.get("modifier", 0) == 0:
                continue
            vk = _keysym_to_vk(cfg["key"])
            if vk is None:
                continue
            hk_id = i + 1
            if user32.RegisterHotKey(None, hk_id, cfg["modifier"], vk):
                registered.append(hk_id)
        msg = ctypes.wintypes.MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:
                break
            if msg.message == WM_HOTKEY:
                idx = int(msg.wParam) - 1
                if 0 <= idx < len(windows) and windows[idx]["hwnd"]:
                    self._sw_queue.put(windows[idx]["hwnd"])
        for hk_id in registered:
            user32.UnregisterHotKey(None, hk_id)
        self._hk_thread_id = None

    def _poll_switch_queue(self):
        try:
            hwnd = self._sw_queue.get_nowait()
            focus_window(hwnd)
        except queue.Empty:
            pass
        self.root.after(50, self._poll_switch_queue)

    def _on_close(self):
        self._stop_hotkey_thread()
        self.root.destroy()

    def _sync_ui(self):
        self._rebuild_my_windows_ui()
        self._rebuild_select_ui()
        self._register_hotkeys()

    # ── Config ────────────────────────────────────────────────────────────────

    def _load_config(self):
        self.hotkey_config = [dict(h) for h in DEFAULT_HOTKEYS]
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            for i, entry in enumerate(data.get("hotkeys", [])[:MAX_HOTKEYS]):
                if isinstance(entry, dict) and "modifier" in entry and "key" in entry:
                    self.hotkey_config[i] = entry
        except Exception:
            pass

    def _write_config(self):
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"hotkeys": self.hotkey_config}, f, indent=2)
        os.replace(tmp, CONFIG_FILE)

    def _combo_str(self, modifier, key):
        """Return a display string for a hotkey combo, e.g. 'Ctrl+Alt+1'."""
        if not key or modifier == 0:
            return "None"
        parts = []
        if modifier & MOD_WIN:     parts.append("Win")
        if modifier & MOD_CONTROL: parts.append("Ctrl")
        if modifier & MOD_ALT:     parts.append("Alt")
        if modifier & MOD_SHIFT:   parts.append("Shift")
        parts.append(key.upper() if len(key) == 1 else key)
        return "+".join(parts)

    # ── Presets ───────────────────────────────────────────────────────────────

    def _load_presets(self):
        if os.path.exists(PRESETS_FILE):
            try:
                with open(PRESETS_FILE) as f:
                    self.presets = json.load(f)
            except Exception:
                self.presets = {}

    def _write_presets(self):
        tmp = PRESETS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.presets, f, indent=2)
        os.replace(tmp, PRESETS_FILE)

    def _save_as_preset(self):
        if not self.selected:
            messagebox.showinfo("Nothing selected",
                "Select some windows first.", parent=self.root)
            return
        name = simpledialog.askstring("Save Preset", "Preset name:", parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        if len(name) > 100:
            messagebox.showerror("Invalid name",
                "Preset name must be 100 characters or fewer.", parent=self.root)
            return
        if any(ord(c) < 32 for c in name):
            messagebox.showerror("Invalid name",
                "Preset name contains invalid characters.", parent=self.root)
            return
        if name in self.presets:
            if not messagebox.askyesno("Overwrite?",
                    f'"{name}" already exists. Overwrite?', parent=self.root):
                return
        self.presets[name] = [w["title"] for w in self.selected]
        self._write_presets()
        self._rebuild_preset_bar()

    def _apply_preset(self, name):
        titles    = self.presets.get(name, [])
        title_map = {t: h for h, t in self.all_wins}
        self.selected = [{"hwnd": title_map.get(t, 0), "title": t} for t in titles]
        self._sync_ui()

    def _delete_preset(self, name):
        if messagebox.askyesno("Delete", f'Delete preset "{name}"?', parent=self.root):
            del self.presets[name]
            self._write_presets()
            self._rebuild_preset_bar()

    # ── Window list ───────────────────────────────────────────────────────────

    def refresh(self):
        self._refresh_btn.config(state=tk.DISABLED, text="↻  Refreshing…")
        def _worker():
            wins = get_all_windows()
            self.root.after(0, lambda: self._finish_refresh(wins))
        threading.Thread(target=_worker, daemon=True).start()

    def _finish_refresh(self, wins):
        self.all_wins = wins
        title_map = {t: h for h, t in self.all_wins}
        for w in self.selected:
            w["hwnd"] = title_map.get(w["title"], 0)
        self._sync_ui()
        self._refresh_btn.config(state=tk.NORMAL, text="↻  Refresh")

    def _toggle_window(self, hwnd, title):
        if any(w["hwnd"] == hwnd for w in self.selected):
            self.selected = [w for w in self.selected if w["hwnd"] != hwnd]
        else:
            self.selected.append({"hwnd": hwnd, "title": title})
        self._sync_ui()

    def _move(self, index, direction):
        target = index + direction
        if 0 <= target < len(self.selected):
            self.selected[index], self.selected[target] = (
                self.selected[target], self.selected[index])
            self._rebuild_my_windows_ui()
            self._register_hotkeys()

    def _remove_selected(self, hwnd):
        self.selected = [w for w in self.selected if w["hwnd"] != hwnd]
        self._sync_ui()

    # ── UI build (once) ───────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        self._build_preset_bar()
        self._build_tabs()

    def _build_header(self):
        hdr = tk.Frame(self.root, bg=C_SURFACE, pady=0)
        hdr.pack(fill=tk.X)

        inner = tk.Frame(hdr, bg=C_SURFACE, pady=14, padx=18)
        inner.pack(fill=tk.X)

        if self._logo_small:
            tk.Label(inner, image=self._logo_small, bg=C_SURFACE).pack(side=tk.LEFT)
        tk.Label(inner, text="WinPilotX",
                 bg=C_SURFACE, fg=C_TITLE,
                 font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT, padx=(8, 0))

        self._refresh_btn = tk.Button(inner, text="↻  Refresh", command=self.refresh,
                                      bg=C_NAV, fg=C_TEXT,
                                      activebackground=C_NAV_HV, activeforeground=C_TEXT,
                                      relief=tk.FLAT, padx=14, pady=6,
                                      font=("Segoe UI", 9), cursor="hand2")
        self._refresh_btn.pack(side=tk.RIGHT)
        _hover(self._refresh_btn, C_NAV, C_NAV_HV)

        tk.Frame(hdr, bg=C_BORDER, height=1).pack(fill=tk.X)

    def _build_preset_bar(self):
        self._preset_outer = tk.Frame(self.root, bg=C_BG, pady=10, padx=18)
        self._preset_outer.pack(fill=tk.X)
        self.preset_bar = tk.Frame(self._preset_outer, bg=C_BG)
        self.preset_bar.pack(fill=tk.X)
        self._rebuild_preset_bar()
        tk.Frame(self.root, bg=C_BORDER, height=1).pack(fill=tk.X)

    def _build_tabs(self):
        self._active_tab = "my"

        tab_bar = tk.Frame(self.root, bg=C_BG)
        tab_bar.pack(fill=tk.X)

        def _tab_wrap(label, tab_id):
            wrap = tk.Frame(tab_bar, bg=C_BG)
            wrap.pack(side=tk.LEFT)
            btn = tk.Button(wrap, text=label,
                            command=lambda: self._switch_tab(tab_id),
                            bg=C_BG, fg=C_MUTED,
                            activebackground=C_BG, activeforeground=C_TEXT,
                            relief=tk.FLAT, padx=22, pady=12,
                            font=("Segoe UI", 10), cursor="hand2",
                            borderwidth=0)
            btn.pack()
            ind = tk.Frame(wrap, bg=C_BG, height=2)
            ind.pack(fill=tk.X)
            return btn, ind

        self._btn_my,       self._ind_my       = _tab_wrap("My Windows",    "my")
        self._btn_sel,      self._ind_sel      = _tab_wrap("Select Windows", "select")
        self._btn_settings, self._ind_settings = _tab_wrap("Settings",      "settings")

        tk.Frame(self.root, bg=C_BORDER, height=1).pack(fill=tk.X)

        self._my_tab       = self._build_my_tab()
        self._sel_tab      = self._build_sel_tab()
        self._settings_tab = self._build_settings_tab()

        # Single routed handler — replaces the two bind_all calls that caused double-scrolling
        def _on_scroll(event):
            if self._active_tab == "my":
                self._my_canvas.yview_scroll(-1 * (event.delta // 120), "units")
            elif self._active_tab == "select":
                self._sel_canvas.yview_scroll(-1 * (event.delta // 120), "units")
        self.root.bind_all("<MouseWheel>", _on_scroll)

        self._switch_tab("my")

    def _switch_tab(self, tab):
        self._active_tab = tab
        self._my_tab.pack_forget()
        self._sel_tab.pack_forget()
        self._settings_tab.pack_forget()
        for btn, ind in [
            (self._btn_my,       self._ind_my),
            (self._btn_sel,      self._ind_sel),
            (self._btn_settings, self._ind_settings),
        ]:
            btn.config(fg=C_MUTED, font=("Segoe UI", 10))
            ind.config(bg=C_BG)
        if tab == "my":
            self._my_tab.pack(fill=tk.BOTH, expand=True)
            self._btn_my.config(fg=C_TEXT, font=("Segoe UI", 10, "bold"))
            self._ind_my.config(bg=C_ACCENT)
        elif tab == "select":
            self._sel_tab.pack(fill=tk.BOTH, expand=True)
            self._btn_sel.config(fg=C_TEXT, font=("Segoe UI", 10, "bold"))
            self._ind_sel.config(bg=C_ACCENT)
        elif tab == "settings":
            self._settings_tab.pack(fill=tk.BOTH, expand=True)
            self._btn_settings.config(fg=C_TEXT, font=("Segoe UI", 10, "bold"))
            self._ind_settings.config(bg=C_ACCENT)

    def _build_my_tab(self):
        frame = tk.Frame(self.root, bg=C_BG)

        toolbar = tk.Frame(frame, bg=C_BG, pady=10, padx=18)
        toolbar.pack(fill=tk.X)
        tk.Label(toolbar, text="MY WINDOWS", bg=C_BG, fg=C_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)
        save_btn = tk.Button(toolbar, text="+ Save as Preset",
                             command=self._save_as_preset,
                             bg=C_ACCENT_BG, fg=C_ACCENT,
                             activebackground=C_ACCENT_HV, activeforeground=C_ACCENT,
                             relief=tk.FLAT, padx=12, pady=5,
                             font=("Segoe UI", 9), cursor="hand2")
        save_btn.pack(side=tk.RIGHT)
        _hover(save_btn, C_ACCENT_BG, C_ACCENT_HV)

        container = tk.Frame(frame, bg=C_BG)
        container.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self._my_canvas, my_sb, self._my_inner, self._my_win_id = \
            _scrollable_area(container, C_BG)

        self._my_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        my_sb.pack(side=tk.RIGHT, fill=tk.Y)
        return frame

    def _build_sel_tab(self):
        frame = tk.Frame(self.root, bg=C_BG)

        toolbar = tk.Frame(frame, bg=C_BG, pady=10, padx=18)
        toolbar.pack(fill=tk.X)
        tk.Label(toolbar, text="ALL WINDOWS  —  click a row to add it to My Windows",
                 bg=C_BG, fg=C_MUTED, font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)

        container = tk.Frame(frame, bg=C_BG)
        container.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self._sel_canvas, sel_sb, self._sel_inner, self._sel_win_id = \
            _scrollable_area(container, C_BG)

        self._sel_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sel_sb.pack(side=tk.RIGHT, fill=tk.Y)
        return frame

    def _build_settings_tab(self):
        frame = tk.Frame(self.root, bg=C_BG)

        toolbar = tk.Frame(frame, bg=C_BG, pady=10, padx=18)
        toolbar.pack(fill=tk.X)
        tk.Label(toolbar,
                 text="HOTKEY SETTINGS  —  click Rebind to assign a custom shortcut",
                 bg=C_BG, fg=C_MUTED, font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)

        self._settings_rows_frame = tk.Frame(frame, bg=C_BG)
        self._settings_rows_frame.pack(fill=tk.X, padx=12, pady=(0, 4))

        footer = tk.Frame(frame, bg=C_BG, padx=12, pady=8)
        footer.pack(fill=tk.X)
        reset_btn = tk.Button(footer, text="Reset All to Defaults",
                              command=self._reset_hotkeys,
                              bg=C_NAV, fg=C_MUTED,
                              activebackground=C_NAV_HV, activeforeground=C_TEXT,
                              relief=tk.FLAT, padx=14, pady=6,
                              font=("Segoe UI", 9), cursor="hand2")
        reset_btn.pack(side=tk.LEFT)
        _hover(reset_btn, C_NAV, C_NAV_HV, C_MUTED, C_TEXT)

        self._rebuild_settings_ui()
        return frame

    def _reset_hotkeys(self):
        self.hotkey_config = [dict(h) for h in DEFAULT_HOTKEYS]
        self._write_config()
        self._rebuild_settings_ui()
        self._rebuild_my_windows_ui()
        self._register_hotkeys()

    def _rebind_hotkey(self, slot_index):
        dlg = tk.Toplevel(self.root)
        dlg.title(f"Rebind Slot {slot_index + 1}")
        dlg.geometry("400x200")
        dlg.resizable(False, False)
        dlg.configure(bg=C_BG)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)

        cfg     = self.hotkey_config[slot_index]
        cur_str = self._combo_str(cfg["modifier"], cfg["key"])

        tk.Label(dlg, text=f"Slot {slot_index + 1}  —  Current: {cur_str}",
                 bg=C_BG, fg=C_MUTED, font=("Segoe UI", 9)).pack(pady=(18, 6))

        status_var = tk.StringVar(value="Hold modifiers, then press a key…")
        tk.Label(dlg, textvariable=status_var,
                 bg=C_SURFACE, fg=C_TEXT,
                 font=("Segoe UI", 10, "bold"),
                 width=36, pady=12).pack(padx=18, pady=(0, 8))

        tk.Label(dlg, text="Letters, digits, F1–F12  •  Esc to cancel",
                 bg=C_BG, fg=C_MUTED, font=("Segoe UI", 8)).pack()

        btn_row = tk.Frame(dlg, bg=C_BG, pady=10)
        btn_row.pack()

        clear_btn = tk.Button(btn_row, text="Clear Binding",
                              bg=C_NAV, fg=C_MUTED,
                              activebackground=C_NAV_HV, activeforeground=C_TEXT,
                              relief=tk.FLAT, padx=12, pady=5,
                              font=("Segoe UI", 9), cursor="hand2")
        clear_btn.pack(side=tk.LEFT, padx=(0, 6))
        _hover(clear_btn, C_NAV, C_NAV_HV, C_MUTED, C_TEXT)

        cancel_btn = tk.Button(btn_row, text="Cancel",
                               command=dlg.destroy,
                               bg=C_NAV, fg=C_MUTED,
                               activebackground=C_NAV_HV, activeforeground=C_TEXT,
                               relief=tk.FLAT, padx=12, pady=5,
                               font=("Segoe UI", 9), cursor="hand2")
        cancel_btn.pack(side=tk.LEFT)
        _hover(cancel_btn, C_NAV, C_NAV_HV, C_MUTED, C_TEXT)

        # Track modifier state via press/release — more reliable than event.state on Windows
        ctrl  = [False]
        alt   = [False]
        shift = [False]
        win_  = [False]

        def _update_preview():
            parts = []
            if win_[0]:   parts.append("Win")
            if ctrl[0]:   parts.append("Ctrl")
            if alt[0]:    parts.append("Alt")
            if shift[0]:  parts.append("Shift")
            status_var.set("+".join(parts) + "+…" if parts else "Hold modifiers, then press a key…")

        def _on_press(e):
            sym = e.keysym
            if sym in ("Control_L", "Control_R"): ctrl[0]  = True;  _update_preview(); return
            if sym in ("Alt_L", "Alt_R"):         alt[0]   = True;  _update_preview(); return
            if sym in ("Shift_L", "Shift_R"):     shift[0] = True;  _update_preview(); return
            if sym in ("Super_L", "Super_R"):     win_[0]  = True;  _update_preview(); return
            if sym == "Escape":                   dlg.destroy();     return

            vk = _keysym_to_vk(sym)
            if vk is None:
                status_var.set(f"Unsupported key: {sym}")
                return

            mod = 0
            if win_[0]:   mod |= MOD_WIN
            if ctrl[0]:   mod |= MOD_CONTROL
            if alt[0]:    mod |= MOD_ALT
            if shift[0]:  mod |= MOD_SHIFT

            if mod == 0:
                status_var.set("Add a modifier (Ctrl, Alt, Shift, Win)…")
                return

            key_str = sym.upper() if len(sym) == 1 else sym

            for j, hk in enumerate(self.hotkey_config):
                if j != slot_index and hk.get("modifier") == mod \
                        and hk.get("key", "").upper() == key_str.upper():
                    status_var.set(f"Conflict with Slot {j + 1}! Try another combo.")
                    return

            self.hotkey_config[slot_index] = {"modifier": mod, "key": key_str}
            self._write_config()
            self._rebuild_settings_ui()
            self._rebuild_my_windows_ui()
            self._register_hotkeys()
            dlg.destroy()

        def _on_release(e):
            sym = e.keysym
            if sym in ("Control_L", "Control_R"): ctrl[0]  = False
            if sym in ("Alt_L", "Alt_R"):         alt[0]   = False
            if sym in ("Shift_L", "Shift_R"):     shift[0] = False
            if sym in ("Super_L", "Super_R"):     win_[0]  = False
            _update_preview()

        def _clear():
            self.hotkey_config[slot_index] = {"modifier": 0, "key": ""}
            self._write_config()
            self._rebuild_settings_ui()
            self._rebuild_my_windows_ui()
            self._register_hotkeys()
            dlg.destroy()

        clear_btn.config(command=_clear)
        dlg.bind("<KeyPress>",   _on_press)
        dlg.bind("<KeyRelease>", _on_release)
        dlg.focus_force()

    # ── UI rebuild ────────────────────────────────────────────────────────────

    def _rebuild_preset_bar(self):
        for w in self.preset_bar.winfo_children():
            w.destroy()

        tk.Label(self.preset_bar, text="PRESETS", bg=C_BG, fg=C_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, padx=(0, 12))

        if not self.presets:
            tk.Label(self.preset_bar,
                     text="No presets yet — build your selection and save one.",
                     bg=C_BG, fg=C_MUTED, font=("Segoe UI", 9, "italic")).pack(side=tk.LEFT)
            return

        for name in self.presets:
            chip = tk.Frame(self.preset_bar, bg=C_PRESET_BG)
            chip.pack(side=tk.LEFT, padx=(0, 6))

            apply_btn = tk.Button(chip, text=name,
                                  command=lambda n=name: self._apply_preset(n),
                                  bg=C_PRESET_BG, fg=C_ACCENT,
                                  activebackground=C_PRESET_HV, activeforeground=C_ACCENT,
                                  relief=tk.FLAT, padx=12, pady=5,
                                  font=("Segoe UI", 9), cursor="hand2")
            apply_btn.pack(side=tk.LEFT)
            _hover(apply_btn, C_PRESET_BG, C_PRESET_HV)

            del_btn = tk.Button(chip, text="×",
                                command=lambda n=name: self._delete_preset(n),
                                bg=C_PRESET_BG, fg=C_MUTED,
                                activebackground=C_RED_HV, activeforeground=C_RED,
                                relief=tk.FLAT, padx=7, pady=5,
                                font=("Segoe UI", 9), cursor="hand2")
            del_btn.pack(side=tk.LEFT)
            _hover(del_btn, C_PRESET_BG, C_RED_HV, C_MUTED, C_RED)

    def _rebuild_my_windows_ui(self):
        for w in self._my_inner.winfo_children():
            w.destroy()

        if not self.selected:
            tk.Label(self._my_inner,
                     text='No windows in your list yet.\nSwitch to "Select Windows" and click rows to add them.',
                     bg=C_BG, fg=C_MUTED,
                     font=("Segoe UI", 10, "italic"), justify=tk.CENTER).pack(pady=50)
            return

        for i, win in enumerate(self.selected):
            alive = is_window_alive(win["hwnd"])
            self._my_row(i, win, alive)

    def _my_row(self, i, win, alive):
        row = tk.Frame(self._my_inner, bg=C_SURFACE, pady=0)
        row.pack(fill=tk.X, pady=1)

        tk.Frame(row, bg=C_GREEN if alive else C_GRAY_DOT, width=3).pack(
            side=tk.LEFT, fill=tk.Y)

        inner = tk.Frame(row, bg=C_SURFACE, pady=11, padx=10)
        inner.pack(side=tk.LEFT, fill=tk.X, expand=True)

        nav = tk.Frame(inner, bg=C_SURFACE)
        nav.pack(side=tk.LEFT, padx=(0, 10))
        for symbol, direction in [("↑", -1), ("↓", 1)]:
            b = tk.Button(nav, text=symbol,
                          command=lambda idx=i, d=direction: self._move(idx, d),
                          bg=C_NAV, fg=C_MUTED,
                          activebackground=C_NAV_HV, activeforeground=C_TEXT,
                          relief=tk.FLAT, width=2, pady=2, cursor="hand2",
                          font=("Segoe UI", 9))
            b.pack(side=tk.LEFT, padx=1)
            _hover(b, C_NAV, C_NAV_HV, C_MUTED, C_TEXT)

        tk.Label(inner, text=win["title"], bg=C_SURFACE,
                 fg=C_TEXT if alive else C_MUTED,
                 font=("Segoe UI", 10), anchor="w").pack(
                     side=tk.LEFT, fill=tk.X, expand=True)

        if i < MAX_HOTKEYS:
            cfg   = self.hotkey_config[i]
            combo = self._combo_str(cfg["modifier"], cfg["key"])
            tk.Label(inner, text=f" {combo} ",
                     bg=C_AMBER_BG, fg=C_AMBER,
                     font=("Segoe UI", 8), padx=2).pack(side=tk.LEFT, padx=(8, 0))

        sw = tk.Button(inner, text="Switch",
                       command=lambda h=win["hwnd"]: focus_window(h),
                       bg=C_ACCENT_BG, fg=C_ACCENT,
                       activebackground=C_ACCENT_HV, activeforeground=C_ACCENT,
                       relief=tk.FLAT, padx=14, pady=4,
                       font=("Segoe UI", 9), cursor="hand2",
                       state=tk.NORMAL if alive else tk.DISABLED)
        sw.pack(side=tk.LEFT, padx=(10, 4))
        if alive:
            _hover(sw, C_ACCENT_BG, C_ACCENT_HV)

        rm = tk.Button(inner, text="×",
                       command=lambda h=win["hwnd"]: self._remove_selected(h),
                       bg=C_SURFACE, fg=C_MUTED,
                       activebackground=C_RED_HV, activeforeground=C_RED,
                       relief=tk.FLAT, padx=8, pady=4,
                       font=("Segoe UI", 11), cursor="hand2")
        rm.pack(side=tk.LEFT)
        _hover(rm, C_SURFACE, C_RED_HV, C_MUTED, C_RED)

    def _rebuild_select_ui(self):
        for w in self._sel_inner.winfo_children():
            w.destroy()

        selected_hwnds = {w["hwnd"] for w in self.selected}

        if not self.all_wins:
            tk.Label(self._sel_inner, text="No windows detected. Click Refresh.",
                     bg=C_BG, fg=C_MUTED, font=("Segoe UI", 10)).pack(pady=20)
            return

        for hwnd, title in self.all_wins:
            is_sel = hwnd in selected_hwnds
            self._sel_row(hwnd, title, is_sel)

    def _sel_row(self, hwnd, title, is_sel):
        row = tk.Frame(self._sel_inner, bg=C_SURFACE, cursor="hand2")
        row.pack(fill=tk.X, pady=1)

        strip = tk.Frame(row, bg=C_ACCENT if is_sel else C_SURFACE, width=3)
        strip.pack(side=tk.LEFT, fill=tk.Y)

        inner = tk.Frame(row, bg=C_SURFACE, pady=10, padx=12)
        inner.pack(side=tk.LEFT, fill=tk.X, expand=True)

        dot = tk.Label(inner, text="●",
                       bg=C_SURFACE, fg=C_ACCENT if is_sel else C_GRAY_DOT,
                       font=("Segoe UI", 8))
        dot.pack(side=tk.LEFT, padx=(0, 10))

        lbl = tk.Label(inner, text=title, bg=C_SURFACE,
                       fg=C_TEXT if is_sel else C_MUTED,
                       font=("Segoe UI", 10), anchor="w")
        lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        hover_targets = [row, inner, dot, lbl]
        def on_enter(e, ws=hover_targets):
            for w in ws:
                try: w.config(bg=C_HOVER)
                except tk.TclError: pass
        def on_leave(e, ws=hover_targets):
            for w in ws:
                try: w.config(bg=C_SURFACE)
                except tk.TclError: pass

        on_click = lambda e, h=hwnd, t=title: self._toggle_window(h, t)
        for w in [row, inner, dot, lbl, strip]:
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)
            w.bind("<Button-1>", on_click)

    def _rebuild_settings_ui(self):
        for w in self._settings_rows_frame.winfo_children():
            w.destroy()
        for i, cfg in enumerate(self.hotkey_config):
            self._settings_row(i, cfg)

    def _settings_row(self, i, cfg):
        row = tk.Frame(self._settings_rows_frame, bg=C_SURFACE, pady=0)
        row.pack(fill=tk.X, pady=1)

        tk.Frame(row, bg=C_ACCENT_BG, width=3).pack(side=tk.LEFT, fill=tk.Y)

        inner = tk.Frame(row, bg=C_SURFACE, pady=10, padx=14)
        inner.pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Label(inner, text=f"Slot {i + 1}",
                 bg=C_SURFACE, fg=C_MUTED,
                 font=("Segoe UI", 9, "bold"), width=6, anchor="w").pack(side=tk.LEFT)

        has_binding = cfg.get("modifier", 0) != 0 and cfg.get("key", "")
        combo = self._combo_str(cfg["modifier"], cfg["key"])
        tk.Label(inner, text=f" {combo} ",
                 bg=C_AMBER_BG if has_binding else C_SURFACE,
                 fg=C_AMBER if has_binding else C_MUTED,
                 font=("Segoe UI", 9), padx=4,
                 anchor="w").pack(side=tk.LEFT, padx=(10, 0), fill=tk.X, expand=True)

        rebind_btn = tk.Button(inner, text="Rebind",
                               command=lambda idx=i: self._rebind_hotkey(idx),
                               bg=C_ACCENT_BG, fg=C_ACCENT,
                               activebackground=C_ACCENT_HV, activeforeground=C_ACCENT,
                               relief=tk.FLAT, padx=12, pady=3,
                               font=("Segoe UI", 9), cursor="hand2")
        rebind_btn.pack(side=tk.LEFT, padx=(10, 0))
        _hover(rebind_btn, C_ACCENT_BG, C_ACCENT_HV)

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
