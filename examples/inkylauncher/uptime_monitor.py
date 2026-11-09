"""
Uptime monitor, using the UptimeRobot API (v3).

Monitors tagged "inky-full" in UptimeRobot get a row with a 24 hour bar (one segment
per hour) and their 24 hour uptime. Monitors tagged only "inky-alert" are shown in a
compact strip that just says whether they're up right now.

Press C to cycle through the colour themes: green/red, then blue/yellow and
green/yellow (colour-blind friendly).

Create a read-only API key in UptimeRobot (Integrations > API) and add it to secrets.py:
UPTIMEROBOT_API_KEY = "..."
"""

import gc
import time

import inky_frame
import inky_helper as ih
import ntptime
import urequests
from inky_frame import BLACK, BLUE, GREEN, RED, WHITE, YELLOW

try:
    from secrets import UPTIMEROBOT_API_KEY
except ImportError:
    UPTIMEROBOT_API_KEY = None

graphics = None
WIDTH = None
HEIGHT = None

# Length of time between updates in minutes.
# Frequent updates will reduce battery life!
UPDATE_INTERVAL = 10

API_URL = "https://api.uptimerobot.com/v3"
FULL_TAG = "inky-full"
ALERT_TAG = "inky-alert"

WINDOW_HOURS = 24
# Fetch incidents from this far back, so ones that started before the window are included
LOOKBACK_DAYS = 3

DOWN_STATUSES = ("DOWN", "LOOKS_DOWN")

FOOTER_H = 22
HEADER_H = 40
ALERT_H = 28

# Colour themes. "bad_text" is the colour for text about something that's down, and
# "highlight" (if set) is drawn behind that text, since yellow text on white is unreadable.
# "outline" draws a black border round "bad" bar segments, so yellow stands out from white.
THEMES = {
    "standard": {"good": GREEN, "bad": RED, "bad_text": RED, "highlight": None, "outline": False},
    "blue-yellow": {"good": BLUE, "bad": YELLOW, "bad_text": BLACK, "highlight": YELLOW, "outline": True},
    "green-yellow": {"good": GREEN, "bad": YELLOW, "bad_text": BLACK, "highlight": YELLOW, "outline": True},
}
THEME_ORDER = ("standard", "blue-yellow", "green-yellow")
THEME_BUTTON = "c"
THEME_FILE = "/uptime_theme.txt"

monitors = None  # list of dicts, see update(); None until the first successful update
updated_at = None
error = None
checked_wake_button = False


def load_theme():
    try:
        with open(THEME_FILE) as f:
            name = f.read().strip()
        if name in THEMES:
            return name
    except OSError:
        pass
    return THEME_ORDER[0]


theme_name = load_theme()


def next_theme():
    global theme_name
    theme_name = THEME_ORDER[(THEME_ORDER.index(theme_name) + 1) % len(THEME_ORDER)]
    try:
        with open(THEME_FILE, "w") as f:
            f.write(theme_name)
    except OSError as e:
        print("Unable to save theme:", e)


def on_button(name):
    # Called by inky_helper while waiting between updates on USB power
    if name == THEME_BUTTON:
        next_theme()
        if monitors is not None:
            ih.led_warn.on()
            draw()
            ih.led_warn.off()


def iso_to_epoch(s):
    # "2026-11-09T07:10:53.580Z" (UTC)
    return time.mktime((int(s[0:4]), int(s[5:7]), int(s[8:10]),
                        int(s[11:13]), int(s[14:16]), int(s[17:19]), 0, 0))


def epoch_to_iso(t):
    y, mo, d, h, mi, s = time.gmtime(t)[:6]
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % (y, mo, d, h, mi, s)


def api_get(path):
    r = urequests.get(API_URL + path, headers={"Authorization": "Bearer " + UPTIMEROBOT_API_KEY})
    try:
        if r.status_code != 200:
            raise OSError("HTTP %d" % r.status_code)
        return r.json()
    finally:
        r.close()


def update():
    global monitors, updated_at, error, checked_wake_button

    # On battery, pressing C wakes the board: switch theme as well as refreshing
    if not checked_wake_button:
        checked_wake_button = True
        if inky_frame.button_c.raw():
            next_theme()

    if not UPTIMEROBOT_API_KEY:
        error = "Add UPTIMEROBOT_API_KEY to secrets.py"
        return

    # Without a connection the request can block forever; keep any previous data instead.
    if not ih.is_connected():
        error = "No network connection"
        return

    try:
        # The incident times are UTC, so make sure the clock is too
        try:
            ntptime.settime()
        except OSError:
            print("Unable to contact NTP server")
        now = time.time()

        # Only the fields we need are kept: the response also includes monitor-specific API keys
        j = api_get("/monitors?tags=%s,%s&limit=200" % (FULL_TAG, ALERT_TAG))
        mons = []
        for m in j["data"]:
            tags = [t["name"] for t in m.get("tags") or []]
            mons.append({
                "id": m["id"],
                "name": m["friendlyName"],
                "status": m["status"],
                "full": FULL_TAG in tags,
                "since": now - (m.get("currentStateDuration") or 0),
                "down": [],
            })
        del j
        gc.collect()

        by_id = {}
        for m in mons:
            by_id[m["id"]] = m

        j = api_get("/incidents?started_after=%s&limit=100" % epoch_to_iso(now - LOOKBACK_DAYS * 86400))
        for inc in j["data"]:
            m = by_id.get((inc.get("monitor") or {}).get("id"))
            if m is None or inc.get("type") != "Downtime":
                continue
            start = iso_to_epoch(inc["startedAt"])
            end = iso_to_epoch(inc["resolvedAt"]) if inc.get("resolvedAt") else now
            m["down"].append((start, end))
        del j
        gc.collect()

        # A monitor that's been down since before the lookback won't have an incident in the list
        for m in mons:
            if m["status"] in DOWN_STATUSES:
                m["down"].append((m["since"], now))

        mons.sort(key=lambda m: m["name"].lower())
        monitors = mons
        updated_at = now
        error = None
    except Exception as e:  # network errors, bad JSON, API errors
        print("Uptime update failed:", e)
        error = "Unable to reach UptimeRobot"


def overlap(intervals, start, end):
    # Total seconds of the (possibly overlapping) intervals that fall within start..end
    clipped = sorted((max(s, start), min(e, end)) for s, e in intervals if e > start and s < end)
    total = 0
    cur_s = cur_e = None
    for s, e in clipped:
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def fit_text(text, max_w, scale):
    if graphics.measure_text(text, scale) <= max_w:
        return text
    while text and graphics.measure_text(text + "..", scale) > max_w:
        text = text[:-1]
    return text + ".."


def status_text(text, x, y, down, theme):
    # Text about a monitor: black normally, or the theme's "bad" style when it's down
    if down and theme["highlight"] is not None:
        graphics.set_pen(theme["highlight"])
        graphics.rectangle(x - 3, y - 2, graphics.measure_text(text, 2) + 6, 20)
    graphics.set_pen(theme["bad_text"] if down else BLACK)
    graphics.text(text, x, y, WIDTH, 2)


def draw_dot(x, y, r, status, theme):
    # Black outline so a light colour (yellow) still shows up on white
    graphics.set_pen(BLACK)
    graphics.circle(x, y, r + 1)
    if status in DOWN_STATUSES:
        graphics.set_pen(theme["bad"])
    elif status == "UP":
        graphics.set_pen(theme["good"])
    else:
        graphics.set_pen(WHITE)
    graphics.circle(x, y, r)


def draw_header(up, total, theme):
    graphics.set_pen(BLACK)
    graphics.text("Uptime", 10, 8, WIDTH, 3)
    if updated_at is not None:
        t = time.gmtime(updated_at)
        right = "%d/%d up  %02d:%02d UTC" % (up, total, t[3], t[4])
    else:
        right = ""
    if error:
        right = error + ("  (as of %s)" % right.split("  ")[-1] if right else "")
    status_text(right, WIDTH - 10 - graphics.measure_text(right, 2), 14, bool(error), theme)
    graphics.set_pen(BLACK)
    graphics.line(0, HEADER_H, WIDTH, HEADER_H)


def draw_row(m, y, row_h, window_start, now, grey, theme):
    mid = y + row_h // 2
    text_y = mid - 8
    down = m["status"] in DOWN_STATUSES

    draw_dot(14, mid, 6, m["status"], theme)

    # Name
    name_x, bar_x, pct_w = 28, 350, 90
    status_text(fit_text(m["name"], bar_x - name_x - 10, 2), name_x, text_y, down, theme)

    # 24 hour bar: one segment per clock hour, oldest on the left, the last being the
    # current (partial) hour. Aligning to clock hours keeps a short incident in one segment.
    bar_w = WIDTH - bar_x - pct_w - 10
    bar_h = max(6, row_h - 10)
    bar_y = mid - bar_h // 2
    paused = [(m["since"], now)] if m["status"] == "PAUSED" else []
    first_hour = (now // 3600 - (WINDOW_HOURS - 1)) * 3600
    for h in range(WINDOW_HOURS):
        seg_start = first_hour + h * 3600
        seg_end = min(seg_start + 3600, now)
        x0 = bar_x + h * bar_w // WINDOW_HOURS
        x1 = bar_x + (h + 1) * bar_w // WINDOW_HOURS - 2
        if overlap(m["down"], seg_start, seg_end) > 0:
            if theme["outline"]:
                graphics.set_pen(BLACK)
                graphics.rectangle(x0, bar_y, x1 - x0, bar_h)
                graphics.set_pen(theme["bad"])
                graphics.rectangle(x0 + 1, bar_y + 1, x1 - x0 - 2, bar_h - 2)
                continue
            graphics.set_pen(theme["bad"])
        elif overlap(paused, seg_start, seg_end) > 0:
            graphics.set_pen(grey)
        else:
            graphics.set_pen(theme["good"])
        graphics.rectangle(x0, bar_y, x1 - x0, bar_h)

    # Uptime over the window, ignoring time spent paused
    window = now - window_start - overlap(paused, window_start, now)
    if window > 0:
        pct = 100.0 * (window - overlap(m["down"], window_start, now)) / window
        text = "%.2f%%" % pct
    else:
        text = "paused"
    status_text(text, WIDTH - 8 - graphics.measure_text(text, 2), text_y, down, theme)


def draw_alerts(alerts, y, theme):
    graphics.set_pen(BLACK)
    graphics.line(0, y, WIDTH, y)
    text_y = y + (ALERT_H - 16) // 2
    graphics.text("Alerts:", 8, text_y, WIDTH, 2)
    x = 8 + graphics.measure_text("Alerts:", 2) + 14
    for m in alerts:
        down = m["status"] in DOWN_STATUSES
        name = m["name"]
        name_w = graphics.measure_text(name, 2)
        if x + 18 + name_w > WIDTH - 8:
            graphics.set_pen(BLACK)
            graphics.text("..", x, text_y, WIDTH, 2)
            break
        draw_dot(x + 6, y + ALERT_H // 2, 6, m["status"], theme)
        status_text(name, x + 18, text_y, down, theme)
        x += 18 + name_w + 20


def draw_message(lines):
    graphics.set_pen(RED)
    graphics.rectangle(0, (HEIGHT // 2) - 20, WIDTH, 40)
    graphics.set_pen(WHITE)
    for i, line in enumerate(lines):
        graphics.text(line, 5, (HEIGHT // 2) - 15 + 17 * i, WIDTH, 2)


def draw():
    theme = THEMES[theme_name]
    graphics.set_font("bitmap8")
    graphics.set_pen(WHITE)
    graphics.clear()

    if monitors is None:
        draw_message(["Unable to display uptime!", error or "No data yet"])
    else:
        now = updated_at
        window_start = now - WINDOW_HOURS * 3600
        full = [m for m in monitors if m["full"]]
        alerts = [m for m in monitors if not m["full"]]
        up = sum(1 for m in monitors if m["status"] == "UP")
        active = sum(1 for m in monitors if m["status"] != "PAUSED")
        draw_header(up, active, theme)

        alert_y = HEIGHT - FOOTER_H - (ALERT_H if alerts else 0)
        rows_top = HEADER_H + 4
        if full:
            row_h = min(40, (alert_y - rows_top - 4) // len(full))
            grey = graphics.create_pen(180, 180, 180)
            for i, m in enumerate(full):
                draw_row(m, rows_top + i * row_h, row_h, window_start, now, grey, theme)
        if alerts:
            draw_alerts(alerts, alert_y, theme)

    graphics.set_pen(BLACK)
    graphics.line(0, HEIGHT - FOOTER_H, WIDTH, HEIGHT - FOOTER_H)
    ih.draw_launcher_hint(graphics, WIDTH, HEIGHT, BLACK, None, FOOTER_H,
                          "C: Colours      Hold A + E: Launcher")

    graphics.update()
