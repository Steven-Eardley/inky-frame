import gc
import time
from urllib import urequest

import inky_helper as ih
import jpegdec
import machine
import sdcard
import uos
from inky_frame import BLACK, RED, WHITE

"""
xkcd daily

You *must* insert an SD card into Inky Frame!
We need somewhere to save the jpg for display.

Fetches a pre-processed XKCD daily image from:
https://pimoroni.github.io/feed2image/xkcd-daily.jpg

See https://xkcd.com/ for more webcomics!
"""

graphics = None
WIDTH = None
HEIGHT = None

UPDATE_INTERVAL = 240

gc.collect()  # We're really gonna need that RAM!

FILENAME = "/sd/xkcd-daily.jpg"
ENDPOINT = "https://pimoroni.github.io/feed2image/xkcd-daily.jpg"


def mount_sd(attempts=3):
    # The SD card shares SPI0 with the display; make sure the display's
    # chip select (GP17) is released so it doesn't interfere with card init.
    machine.Pin(17, machine.Pin.OUT).value(1)
    for attempt in range(attempts):
        try:
            sd_spi = machine.SPI(0, sck=machine.Pin(18, machine.Pin.OUT), mosi=machine.Pin(19, machine.Pin.OUT), miso=machine.Pin(16, machine.Pin.OUT))
            sd = sdcard.SDCard(sd_spi, machine.Pin(22))
            uos.mount(sd, "/sd")
            return True
        except OSError as e:
            print("SD mount attempt %d failed: %r" % (attempt + 1, e))
            time.sleep(0.5)
    return False


# Don't raise if the card won't mount: the app still needs to run so it can
# show the problem, and so holding A + E can get back to the launcher.
sd_mounted = mount_sd()
gc.collect()  # Claw back some RAM!


def update():
    if not sd_mounted:
        return

    # Without a connection urlopen can block forever; keep the last saved comic instead.
    if not ih.is_connected():
        print("No network connection, skipping download")
        return

    url = ENDPOINT

    if (WIDTH, HEIGHT) != (600, 448):
        url = url.replace("xkcd-", f"xkcd-{WIDTH}x{HEIGHT}-")

    socket = urequest.urlopen(url)

    # Stream the image data from the socket onto disk in 1024 byte chunks
    # the 600x448-ish jpeg will be roughly ~24k, we really don't have the RAM!
    data = bytearray(1024)
    with open(FILENAME, "wb") as f:
        while True:
            if socket.readinto(data) == 0:
                break
            f.write(data)
    socket.close()
    gc.collect()  # We really are tight on RAM!


def draw():

    gc.collect()  # For good measure...

    graphics.set_pen(WHITE)
    graphics.clear()

    if sd_mounted:
        jpeg = jpegdec.JPEG(graphics)
        jpeg.open_file(FILENAME)
        jpeg.decode()
    else:
        graphics.set_pen(RED)
        graphics.rectangle(0, (HEIGHT // 2) - 20, WIDTH, 40)
        graphics.set_pen(WHITE)
        graphics.text("Unable to read the SD card!", 5, (HEIGHT // 2) - 15, WIDTH, 2)
        graphics.text("Check it's inserted, then reset.", 5, (HEIGHT // 2) + 2, WIDTH, 2)

    # Launcher hint along the bottom, stopping short of the QR code / "xkcd.com"
    # label in the bottom-right corner of the image.
    footer_w = WIDTH - 80
    ih.draw_launcher_hint(graphics, footer_w, HEIGHT, BLACK, WHITE, 22)
    graphics.set_pen(BLACK)
    graphics.line(0, HEIGHT - 22, footer_w, HEIGHT - 22)

    graphics.update()
