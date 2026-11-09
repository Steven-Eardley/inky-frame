import gc
import time

import inky_helper as ih
from inky_frame import BLACK, BLUE, GREEN, WHITE
from machine import reset
# Uncomment the line for your Inky Frame display size
# from picographics import PicoGraphics, DISPLAY_INKY_FRAME_4 as DISPLAY  # 4.0"
# from picographics import PicoGraphics, DISPLAY_INKY_FRAME as DISPLAY      # 5.7"
# from picographics import PicoGraphics, DISPLAY_INKY_FRAME_7 as DISPLAY  # 7.3"
from picographics import \
    DISPLAY_INKY_FRAME_SPECTRA_7 as DISPLAY  # 7.3" Spectra
from picographics import PicoGraphics

# Create a secrets.py with your Wifi details to be able to get the time
#
# secrets.py should contain:
# WIFI_SSID = "Your WiFi SSID"
# WIFI_PASSWORD = "Your WiFi password"

# A short delay to give USB chance to initialise
time.sleep(0.5)

# Setup for the display.
graphics = PicoGraphics(DISPLAY)
WIDTH, HEIGHT = graphics.get_bounds()
graphics.set_font("bitmap8")


# Launcher entries: (label, app module, buttons that select it)
APPS = [
    ("A. NASA Picture of the Day", "nasa_apod", ("a",)),
    ("B. Word Clock", "word_clock", ("b",)),
    ("C. Daily XKCD", "daily_xkcd", ("c",)),
    ("D. Headlines", "news_headlines", ("d",)),
    ("E. Carbon Intensity", "carbon_intensity", ("e",)),
    ("F. Uptime (A + B)", "uptime_monitor", ("a", "b")),
]

# How long to wait after the first button press for a second one (for A + B)
COMBO_GRACE_MS = 300


def wait_for_choice():
    buttons = {
        "a": ih.inky_frame.button_a,
        "b": ih.inky_frame.button_b,
        "c": ih.inky_frame.button_c,
        "d": ih.inky_frame.button_d,
        "e": ih.inky_frame.button_e,
    }
    while True:
        # Wait for any button
        pressed = set(k for k, b in buttons.items() if b.raw())
        if not pressed:
            time.sleep(0.02)
            continue

        # Give a second button a moment to join, for combinations like A + B
        deadline = time.ticks_add(time.ticks_ms(), COMBO_GRACE_MS)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            pressed.update(k for k, b in buttons.items() if b.raw())
            time.sleep(0.02)

        for label, app, combo in APPS:
            if pressed == set(combo):
                for k in combo:
                    buttons[k].led_on()
                return app

        # Unrecognised combination: wait for release and start again
        while any(b.raw() for b in buttons.values()):
            time.sleep(0.02)


def launcher():
    # Draws the menu
    graphics.set_pen(WHITE)
    graphics.clear()

    graphics.set_pen(BLUE)
    graphics.rectangle(0, 0, WIDTH, 50)
    graphics.set_pen(WHITE)
    title = "Launcher"
    title_len = graphics.measure_text(title, 4) // 2
    graphics.text(title, (WIDTH // 2 - title_len), 10, WIDTH, 4)

    # Fit the entries between the title bar and the note at the bottom, centred in the space
    title_h, note_y = 50, HEIGHT - 30
    space = note_y - title_h
    step = min(60, (space - 10) // len(APPS))
    row_h = step - 10
    top = title_h + (space - (len(APPS) * step - 10)) // 2
    grey = graphics.create_pen(220, 220, 220)
    for i, (label, app, combo) in enumerate(APPS):
        y = top + i * step
        bar_w = WIDTH - (100 + 50 * i)
        graphics.set_pen(GREEN if i % 2 == 0 else BLUE)
        graphics.rectangle(30, y, bar_w, row_h)
        graphics.set_pen(grey)
        graphics.rectangle(bar_w, y, WIDTH - 30 - bar_w, row_h)
        graphics.set_pen(WHITE)
        graphics.text(label, 35, y + (row_h - 24) // 2, 600, 3)

    graphics.set_pen(BLACK)
    note = "Hold A + E to return to the Launcher"
    note_len = graphics.measure_text(note, 2) // 2
    graphics.text(note, (WIDTH // 2 - note_len), HEIGHT - 30, 600, 2)

    ih.led_warn.on()
    graphics.update()
    ih.led_warn.off()

    # Now we've drawn the menu to the screen, we wait here for the user to select an app.
    # Then once an app is selected, we set that as the current app and reset the device and load into it.
    # To add or replace an app, edit the APPS list above.
    app = wait_for_choice()
    ih.update_state(app)
    time.sleep(0.5)
    reset()


# Turn any LEDs off that may still be on from last run.
ih.clear_button_leds()
ih.led_warn.off()

if ih.launcher_requested():
    launcher()

ih.clear_button_leds()

if ih.file_exists("state.json"):
    # Loads the JSON and launches the app
    ih.load_state()
    ih.launch_app(ih.state["run"])

    # Passes the the graphics object from the launcher to the app
    ih.app.graphics = graphics
    ih.app.WIDTH = WIDTH
    ih.app.HEIGHT = HEIGHT

else:
    launcher()

try:
    from secrets import WIFI_PASSWORD, WIFI_SSID
    ih.network_connect(WIFI_SSID, WIFI_PASSWORD)
except ImportError:
    print("Create secrets.py with your WiFi credentials")

# Get some memory back, we really need it!
gc.collect()

# The main loop executes the update and draw function from the imported app,
# and then goes to sleep ZzzzZZz

file = ih.file_exists("state.json")

print(file)

while True:
    ih.app.update()
    ih.led_warn.on()
    ih.app.draw()
    ih.led_warn.off()
    ih.sleep(ih.app.UPDATE_INTERVAL)
