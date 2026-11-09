import json
import math
import os
import time

import inky_frame
import network
from machine import PWM, Pin, Timer, reset
from pcf85063a import PCF85063A
from pimoroni_i2c import PimoroniI2C

# Pin setup for VSYS_HOLD needed to sleep and wake.
HOLD_VSYS_EN_PIN = 2
hold_vsys_en_pin = Pin(HOLD_VSYS_EN_PIN, Pin.OUT)

# intialise the pcf85063a real time clock chip
I2C_SDA_PIN = 4
I2C_SCL_PIN = 5
i2c = PimoroniI2C(I2C_SDA_PIN, I2C_SCL_PIN, 100000)
rtc = PCF85063A(i2c)

led_warn = Pin(6, Pin.OUT)

# set up for the network LED
network_led_pwm = PWM(Pin(7))
network_led_pwm.freq(1000)
network_led_pwm.duty_u16(0)


# set the brightness of the network led
def network_led(brightness):
    brightness = max(0, min(100, brightness))  # clamp to range
    # gamma correct the brightness (gamma 2.8)
    value = int(pow(brightness / 100.0, 2.8) * 65535.0 + 0.5)
    network_led_pwm.duty_u16(value)


network_led_timer = Timer(-1)
network_led_pulse_speed_hz = 1


def network_led_callback(_t):
    # updates the network led brightness based on a sinusoid seeded by the current time
    brightness = (math.sin(time.ticks_ms() * math.pi * 2 / (1000 / network_led_pulse_speed_hz)) * 40) + 60
    value = int(pow(brightness / 100.0, 2.8) * 65535.0 + 0.5)
    network_led_pwm.duty_u16(value)


# set the network led into pulsing mode
def pulse_network_led(speed_hz=1):
    global network_led_timer, network_led_pulse_speed_hz
    network_led_pulse_speed_hz = speed_hz
    network_led_timer.deinit()
    network_led_timer.init(period=50, mode=Timer.PERIODIC, callback=network_led_callback)


# turn off the network led and disable any pulsing animation that's running
def stop_network_led():
    global network_led_timer
    network_led_timer.deinit()
    network_led_pwm.duty_u16(0)


def sleep(t):
    # Time to have a little nap until the next update
    rtc.clear_timer_flag()
    rtc.set_timer(t, ttp=rtc.TIMER_TICK_1_OVER_60HZ)
    rtc.enable_timer_interrupt(True)

    # Turn off the HOLD VSYS pin
    # this allows the device to go into sleep mode when on battery power.
    hold_vsys_en_pin.value(0)

    # On USB power we're still running, so wait out the interval here, watching for
    # A + E being held to return to the launcher. (On battery, pressing A + E wakes
    # the board and main.py's startup check opens the launcher.)
    wait_for_launcher_hold(60 * t)


LAUNCHER_HOLD_MS = 1000
LAUNCHER_GRACE_MS = 1000


# Checked at startup: were A and E both pressed? Button.raw() first reports whether the
# button was held when the board powered on (how a press wakes it on battery), then its
# live state. The board latches only the buttons down at the instant it powers on, so if
# A and E weren't pressed at exactly the same moment, give the other a short grace period.
def launcher_requested():
    a_seen = inky_frame.button_a.raw()
    e_seen = inky_frame.button_e.raw()
    if a_seen and e_seen:
        return True
    if not (a_seen or e_seen):
        return False
    deadline = time.ticks_add(time.ticks_ms(), LAUNCHER_GRACE_MS)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        a_seen = a_seen or inky_frame.button_a.raw()
        e_seen = e_seen or inky_frame.button_e.raw()
        if a_seen and e_seen:
            return True
        time.sleep(0.02)
    return False


def wait_for_launcher_hold(seconds):
    deadline = time.ticks_add(time.ticks_ms(), int(seconds * 1000))
    held_since = None
    # Apps can handle presses of B, C and D by defining on_button(name)
    other = (("b", inky_frame.button_b), ("c", inky_frame.button_c), ("d", inky_frame.button_d))
    was_down = {}
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        for name, button in other:
            down = button.raw()
            if down and not was_down.get(name):
                handler = getattr(app, "on_button", None)
                if handler:
                    handler(name)
            was_down[name] = down
        if inky_frame.button_a.raw() and inky_frame.button_e.raw():
            if held_since is None:
                held_since = time.ticks_ms()
                inky_frame.button_a.led_on()
                inky_frame.button_e.led_on()
            elif time.ticks_diff(time.ticks_ms(), held_since) >= LAUNCHER_HOLD_MS:
                # With no saved app, main.py shows the launcher after the reset
                clear_state()
                reset()
        elif held_since is not None:
            held_since = None
            inky_frame.button_a.led_off()
            inky_frame.button_e.led_off()
        time.sleep(0.05)


LAUNCHER_HINT = "Hold A + E to return to the Launcher"


# Draws the "return to launcher" button reference in a bar along the bottom of the screen.
# Pass bg=None to draw the text without a background bar.
def draw_launcher_hint(graphics, width, height, fg, bg=None, bar_height=20, text=LAUNCHER_HINT):
    if bg is not None:
        graphics.set_pen(bg)
        graphics.rectangle(0, height - bar_height, width, bar_height)
    graphics.set_pen(fg)
    graphics.set_font("bitmap8")
    text_w = graphics.measure_text(text, 2)
    # bitmap8 at scale 2 is 16px tall; centre it vertically in the bar
    graphics.text(text, (width - text_w) // 2, height - bar_height + (bar_height - 16) // 2, width, 2)


# Turns off the button LEDs
def clear_button_leds():
    inky_frame.button_a.led_off()
    inky_frame.button_b.led_off()
    inky_frame.button_c.led_off()
    inky_frame.button_d.led_off()
    inky_frame.button_e.led_off()


def _try_connect(wlan, SSID, PSK, max_wait):
    wlan.config(pm=0xa11140)  # Turn WiFi power saving off for some slow APs
    wlan.connect(SSID, PSK)

    while max_wait > 0:
        if wlan.status() < 0 or wlan.status() >= 3:
            break
        max_wait -= 1
        print("waiting for connection...")
        time.sleep(1)

    return wlan.status() == 3


def network_connect(SSID, PSK):
    # Enable the Wireless
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    # Sets the Wireless LED pulsing and attempts to connect to your local network.
    pulse_network_led()

    # A reset while connected can leave the radio unable to reconnect on the next boot,
    # so if the first attempt fails, power-cycle the radio and try once more.
    if not _try_connect(wlan, SSID, PSK, 15):
        print("WiFi connect failed (status %d), resetting radio and retrying" % wlan.status())
        wlan.disconnect()
        wlan.active(False)
        time.sleep(1)
        wlan.active(True)
        _try_connect(wlan, SSID, PSK, 15)

    stop_network_led()
    network_led_pwm.duty_u16(30000)

    # Handle connection error. Switches the Warn LED on.
    if wlan.status() != 3:
        stop_network_led()
        led_warn.on()
        return False
    return True


def is_connected():
    return network.WLAN(network.STA_IF).isconnected()


state = {"run": None}
app = None


def file_exists(filename):
    try:
        return (os.stat(filename)[0] & 0x4000) == 0
    except OSError:
        return False


def clear_state():
    if file_exists("state.json"):
        os.remove("state.json")


def save_state(data):
    with open("/state.json", "w") as f:
        f.write(json.dumps(data))
        f.flush()


def load_state():
    global state
    data = json.loads(open("/state.json", "r").read())
    if type(data) is dict:
        state = data


def update_state(running):
    global state
    state["run"] = running
    save_state(state)


def launch_app(app_name):
    global app
    app = __import__(app_name)
    print(app)
    update_state(app_name)
