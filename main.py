#!/usr/bin/env python3

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gdk, Gio
import threading
import time
import sys
import signal
import select

try:
    from evdev import UInput, ecodes, InputDevice, list_devices
except ImportError:
    print("Error: evdev module not found.")
    print("Install with: sudo dnf install python3-evdev")
    print("Or run: ./install_deps.sh")
    sys.exit(1)

class HotkeyListener:
    def __init__(self, callback):
        self.callback = callback
        self.running = False
        self.thread = None
        self.devices = []
        self.f8_pressed = False
        
    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self.listen_loop, daemon=True)
            self.thread.start()
    
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.1)
    
    def listen_loop(self):
        try:
            devices = [InputDevice(path) for path in list_devices()]
            for dev in devices:
                try:
                    caps = dev.capabilities()
                    if ecodes.EV_KEY in caps and ecodes.KEY_F8 in caps[ecodes.EV_KEY]:
                        self.devices.append(dev)
                except Exception:
                    continue
        except Exception:
            pass
        
        if not self.devices:
            return
        
        while self.running:
            try:
                r, w, x = select.select(self.devices, [], [], 0.5)
                if r:
                    for dev in r:
                        try:
                            for event in dev.read():
                                if event.type == ecodes.EV_KEY and event.code == ecodes.KEY_F8:
                                    if event.value == 1 and not self.f8_pressed:
                                        self.f8_pressed = True
                                        GLib.idle_add(self.callback)
                                    elif event.value == 0:
                                        self.f8_pressed = False
                        except Exception:
                            continue
            except Exception:
                continue

class AutoClicker:
    def __init__(self):
        self.running = False
        self.click_thread = None
        self.interval = 0.1
        self.button = ecodes.BTN_LEFT
        self.kill_requested = False
        self.ui = None
        self.hotkey_listener = None
        
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        capabilities = {
            ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE]
        }
        
        try:
            self.device = UInput(capabilities, name="wayclick-virtual-mouse")
        except PermissionError:
            print("Error: Requires uinput permissions. Run: sudo modprobe uinput && sudo usermod -a -G input $USER")
            sys.exit(1)
        except Exception:
            try:
                self.device = UInput()
            except Exception:
                print("Error: Cannot initialize uinput device")
                sys.exit(1)
    
    def signal_handler(self, signum, frame):
        self.stop()
        sys.exit(0)
    
    def click(self):
        try:
            self.device.write(ecodes.EV_KEY, self.button, 1)
            self.device.syn()
            self.device.write(ecodes.EV_KEY, self.button, 0)
            self.device.syn()
        except Exception:
            pass
    
    def click_loop(self):
        while self.running and not self.kill_requested:
            cycle_start = time.perf_counter()
            self.click()
            elapsed = time.perf_counter() - cycle_start
            remaining = self.interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
    
    def start(self):
        if not self.running:
            self.running = True
            self.kill_requested = False
            self.click_thread = threading.Thread(target=self.click_loop, daemon=True)
            self.click_thread.start()
            if self.ui:
                GLib.idle_add(self.ui.update_status, True)
    
    def stop(self):
        if self.running:
            self.running = False
            self.kill_requested = True
            if self.click_thread:
                self.click_thread.join(timeout=0.5)
            if self.ui:
                GLib.idle_add(self.ui.update_status, False)
    
    def set_interval(self, value):
        self.interval = max(0.001, float(value))
    
    def set_button(self, button):
        self.button = button
    
    def set_hotkey_listener(self, listener):
        self.hotkey_listener = listener
    
    def cleanup(self):
        self.stop()
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        try:
            self.device.close()
        except Exception:
            pass

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, clicker):
        super().__init__(application=app, title="WayClick")
        self.clicker = clicker
        clicker.ui = self
        
        self.set_default_size(400, 300)
        self.set_resizable(False)
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        
        interval_label = Gtk.Label(label="Click Interval (seconds)")
        interval_label.set_xalign(0)
        box.append(interval_label)
        
        self.interval_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.001, 2.0, 0.001)
        self.interval_scale.set_value(0.1)
        self.interval_scale.set_draw_value(True)
        self.interval_scale.set_hexpand(True)
        self.interval_scale.connect("value-changed", self.on_interval_changed)
        box.append(self.interval_scale)
        
        button_label = Gtk.Label(label="Mouse Button")
        button_label.set_xalign(0)
        box.append(button_label)
        
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        self.left_button = Gtk.ToggleButton(label="Left")
        self.left_button.set_active(True)
        self.left_button.connect("toggled", self.on_button_toggled, ecodes.BTN_LEFT)
        button_box.append(self.left_button)
        
        self.right_button = Gtk.ToggleButton(label="Right")
        self.right_button.connect("toggled", self.on_button_toggled, ecodes.BTN_RIGHT)
        button_box.append(self.right_button)
        
        self.middle_button = Gtk.ToggleButton(label="Middle")
        self.middle_button.connect("toggled", self.on_button_toggled, ecodes.BTN_MIDDLE)
        button_box.append(self.middle_button)
        
        box.append(button_box)
        
        self.start_button = Gtk.Button(label="Start (F8 to kill)")
        self.start_button.add_css_class("suggested-action")
        self.start_button.connect("clicked", self.on_start_clicked)
        box.append(self.start_button)
        
        self.status_label = Gtk.Label(label="Status: Stopped")
        self.status_label.add_css_class("dim-label")
        box.append(self.status_label)
        
        info_label = Gtk.Label(label="Press F8 anywhere to emergency stop")
        info_label.add_css_class("caption")
        info_label.set_xalign(0)
        box.append(info_label)
        
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(box)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        
        clamp = Adw.Clamp()
        clamp.set_maximum_size(400)
        clamp.set_child(scrolled)
        
        header_bar = Adw.HeaderBar()
        header_bar.set_title_widget(Gtk.Label(label="WayClick"))
        
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_box.append(header_bar)
        main_box.append(clamp)
        
        self.set_content(main_box)
        
        self.setup_global_hotkey()
    
    def setup_global_hotkey(self):
        controller = Gtk.EventControllerKey.new()
        controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(controller)
        
        hotkey_listener = HotkeyListener(self.on_emergency_stop)
        hotkey_listener.start()
        self.clicker.set_hotkey_listener(hotkey_listener)
    
    def on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_F8:
            self.on_emergency_stop()
            return True
        return False
    
    def on_emergency_stop(self, *args):
        if self.clicker.running:
            self.clicker.stop()
            if self.start_button:
                self.start_button.set_label("Start (F8 to kill)")
                self.start_button.remove_css_class("destructive-action")
                self.start_button.add_css_class("suggested-action")
    
    def on_interval_changed(self, scale):
        value = scale.get_value()
        self.clicker.set_interval(value)
    
    def on_button_toggled(self, button, ecode):
        if button.get_active():
            if button == self.left_button:
                self.right_button.set_active(False)
                self.middle_button.set_active(False)
            elif button == self.right_button:
                self.left_button.set_active(False)
                self.middle_button.set_active(False)
            elif button == self.middle_button:
                self.left_button.set_active(False)
                self.right_button.set_active(False)
            self.clicker.set_button(ecode)
    
    def on_start_clicked(self, button):
        if self.clicker.running:
            self.clicker.stop()
            button.set_label("Start (F8 to kill)")
            button.remove_css_class("destructive-action")
            button.add_css_class("suggested-action")
        else:
            self.clicker.start()
            button.set_label("Stop")
            button.remove_css_class("suggested-action")
            button.add_css_class("destructive-action")
    
    def update_status(self, running):
        if running:
            self.status_label.set_text("Status: Running")
        else:
            self.status_label.set_text("Status: Stopped")

class WayClickApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.wayclick.app")
        self.clicker = None
    
    def do_activate(self):
        if not self.clicker:
            self.clicker = AutoClicker()
        
        win = self.props.active_window
        if not win:
            win = MainWindow(self, self.clicker)
        win.present()
    
    def do_shutdown(self):
        if self.clicker:
            self.clicker.cleanup()
        super().do_shutdown()

if __name__ == "__main__":
    app = WayClickApp()
    app.run(sys.argv)

