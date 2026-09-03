#!/usr/bin/env python3
"""Shredograph - 5-Tage-Kite-Windvorhersage fuer ein 800x480 Spectra-6 E-Ink-Display.

Holt die Vorhersage von Open-Meteo, rendert das Layout mit Pillow und gibt ein
auf die sechs Spectra-Farben gedithertes BMP aus, das der ESP32 direkt anzeigen kann.

    python3 shredograph.py                 # live von Open-Meteo
    python3 shredograph.py --demo          # Beispieldaten, ohne Netz
    python3 shredograph.py --out /srv/www  # Zielverzeichnis
"""

import argparse
import datetime as dt
import json
import math
import os
import urllib.parse
import urllib.request
from collections import Counter

from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------
# Konfiguration
# --------------------------------------------------------------------------

SPOT_NAME = "Laboe"
SPOT_SUB = "Kieler Förde"
LAT, LON = 54.4103, 10.2278
TIMEZONE = "Europe/Berlin"
MODEL = "icon_seamless"          # ICON-D2 fuer die ersten 48h, dann ICON-EU/global
DAY_START, DAY_END = 6, 22       # relevantes Zeitfenster je Tag
NUM_DAYS = 5

# Kite-Matrix: (min_kn, max_kn, Label, Farbschluessel)
# Ausgelegt auf 80 kg Fahrergewicht und ein 9er/12er Quiver.
KITE_RULES = [
    (0.0, 16.0, "zu wenig", "muted"),
    (16.0, 21.0, "12 m", "green"),
    (21.0, 28.0, "9 m", "blue"),
    (28.0, 999.0, "zu viel", "red"),
]

W, H = 800, 480
FONT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fonts", "SpaceGrotesk[wght].ttf")

# Spectra-6-Palette. Die Werte sind die vom Waveshare-Konverter benutzten
# Zielfarben - je nach Panel-Charge lohnt sich hier etwas Feintuning.
SPECTRA6 = [
    (0, 0, 0),        # schwarz
    (255, 255, 255),  # weiss
    (255, 243, 56),   # gelb
    (191, 0, 0),      # rot
    (100, 64, 255),   # blau
    (67, 138, 28),    # gruen
]

# Zeichenfarben. Bewusst nah an der Palette, damit Flaechen kaum dithern.
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
YELLOW = (242, 194, 0)
RED = (192, 57, 43)
BLUE = (27, 78, 155)
GREEN = (30, 122, 60)
GRAY = (140, 140, 140)      # wird gedithert - nur fuer Flaechen, nie fuer Text
HAIRLINE = (170, 170, 170)

COLORS = {"blue": BLUE, "green": GREEN, "red": RED, "muted": BLACK}

# --------------------------------------------------------------------------
# Schriften
# --------------------------------------------------------------------------


def load_font(size, weight="Regular"):
    """Laedt einen Schnitt der Variable Font, mit Fallback auf DejaVu."""
    try:
        font = ImageFont.truetype(FONT_FILE, size)
        font.set_variation_by_name(weight)
        return font
    except (OSError, AttributeError):
        fallback = "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"
        return ImageFont.truetype(
            fallback % ("-Bold" if weight in ("Bold", "Medium") else ""), size)


# --------------------------------------------------------------------------
# Daten holen
# --------------------------------------------------------------------------


def fetch_forecast():
    params = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": ("temperature_2m,weather_code,wind_speed_10m,"
                   "wind_gusts_10m,wind_direction_10m"),
        "wind_speed_unit": "kn",
        "timezone": TIMEZONE,
        "models": MODEL,
        "forecast_days": NUM_DAYS + 1,
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def circular_mean(degrees, weights):
    """Vektormittel der Windrichtung - ein arithmetisches Mittel waere bei
    Richtungen um 0 Grad herum schlicht falsch."""
    if not degrees:
        return 0.0
    x = sum(w * math.cos(math.radians(d)) for d, w in zip(degrees, weights))
    y = sum(w * math.sin(math.radians(d)) for d, w in zip(degrees, weights))
    return math.degrees(math.atan2(y, x)) % 360


def compass(deg):
    names = ["N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return names[int((deg + 11.25) % 360 // 22.5)]


def kite_for(wind_kn):
    for lo, hi, label, key in KITE_RULES:
        if lo <= wind_kn < hi:
            return label, key
    return "zu viel", "red"


def aggregate(data):
    """Fasst die Stundenwerte zu Tageskacheln zusammen."""
    hourly = data["hourly"]
    times = [dt.datetime.fromisoformat(t) for t in hourly["time"]]
    by_date = {}
    for i, t in enumerate(times):
        if not DAY_START <= t.hour <= DAY_END:
            continue
        by_date.setdefault(t.date(), []).append(i)

    days = []
    for date in sorted(by_date)[:NUM_DAYS]:
        idx = by_date[date]
        winds = [hourly["wind_speed_10m"][i] for i in idx]
        gusts = [hourly["wind_gusts_10m"][i] for i in idx]
        dirs = [hourly["wind_direction_10m"][i] for i in idx]
        temps = [hourly["temperature_2m"][i] for i in idx]
        codes = [hourly["weather_code"][i] for i in idx]

        peak = max(winds)
        # Verlaufsbalken: alle zwei Stunden ein Wert
        profile = [hourly["wind_speed_10m"][i] for i in idx[::2]][:8]

        label, key = kite_for(peak)
        days.append({
            "date": date,
            "wind": round(peak),
            "gust": round(max(gusts)),
            "dir": circular_mean(dirs, winds),
            "temp": round(max(temps)),
            "code": Counter(codes).most_common(1)[0][0],
            "kite": label,
            "key": key,
            "profile": profile,
        })
    return days


def demo_days():
    """Beispieldaten, damit sich das Layout ohne Netz pruefen laesst."""
    base = dt.date.today()
    raw = [
        (22, 29, 315, 18, 0, [14, 18, 22, 22, 20, 17, 14, 12]),
        (15, 20, 270, 17, 2, [10, 12, 15, 15, 14, 12, 11, 9]),
        (31, 41, 337, 16, 61, [22, 27, 31, 31, 29, 26, 24, 21]),
        (19, 26, 248, 19, 1, [11, 14, 18, 19, 18, 16, 14, 12]),
        (9, 13, 135, 20, 3, [6, 7, 9, 9, 8, 8, 7, 6]),
    ]
    days = []
    for i, (wind, gust, wdir, temp, code, profile) in enumerate(raw):
        label, key = kite_for(wind)
        days.append({
            "date": base + dt.timedelta(days=i + 1),
            "wind": wind, "gust": gust, "dir": wdir, "temp": temp,
            "code": code, "kite": label, "key": key, "profile": profile,
        })
    return days


# --------------------------------------------------------------------------
# Zeichnen
# --------------------------------------------------------------------------

WEEKDAYS = ["MO", "DI", "MI", "DO", "FR", "SA", "SO"]
WEEKDAYS_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def draw_tracked(draw, xy, text, font, fill, tracking=0, anchor="ls"):
    """Text mit Sperrung - Pillow kennt kein letter-spacing."""
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x, y = xy
    if anchor.startswith("m"):
        x -= total / 2
    elif anchor.startswith("r"):
        x -= total
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill, anchor="ls")
        x += w + tracking
    return total


def draw_kite_mark(draw, cx, cy, scale, color, width=3):
    """Die Kite-Silhouette: flacher Bogen plus Leinen zur Bar."""
    hw, hh = 23 * scale, 9 * scale
    draw.arc([cx - hw, cy - hh, cx + hw, cy + hh], start=180, end=360,
             fill=color, width=width)
    draw.line([cx - hw, cy, cx - 3 * scale, cy + 20 * scale], fill=color, width=width)
    draw.line([cx + hw, cy, cx + 3 * scale, cy + 20 * scale], fill=color, width=width)
    draw.line([cx - 8 * scale, cy + 22 * scale, cx + 8 * scale, cy + 22 * scale],
              fill=color, width=width)


def draw_arrow(draw, cx, cy, deg, color, size=11):
    """Pfeil, der zeigt, wohin der Wind weht (Richtung + 180 Grad)."""
    a = math.radians(deg + 180)
    pts = [(0, -size), (size * 0.55, size * 0.36),
           (0, size * 0.09), (-size * 0.55, size * 0.36)]
    rot = [(cx + x * math.cos(a) - y * math.sin(a),
            cy + x * math.sin(a) + y * math.cos(a)) for x, y in pts]
    draw.polygon(rot, fill=color)


def draw_weather(draw, cx, cy, code):
    """Wettersymbol nach WMO-Code."""
    sun, cloud, rain, snow, storm = False, False, False, False, False
    if code in (0, 1):
        sun = True
    elif code == 2:
        sun, cloud = True, True
    elif code in (3, 45, 48):
        cloud = True
    elif code in (71, 73, 75, 77, 85, 86):
        cloud, snow = True, True
    elif code in (95, 96, 99):
        cloud, storm = True, True
    else:
        cloud, rain = True, True

    if sun and not cloud:
        draw.ellipse([cx - 15, cy - 15, cx + 15, cy + 15], fill=YELLOW)
        for dx, dy, ex, ey in [(0, -24, 0, -31), (0, 24, 0, 31),
                               (-24, 0, -31, 0), (24, 0, 31, 0),
                               (-18, -18, -23, -23), (18, -18, 23, -23)]:
            draw.line([cx + dx, cy + dy, cx + ex, cy + ey], fill=YELLOW, width=4)
        return

    if sun:
        draw.ellipse([cx + 9, cy - 17, cx + 31, cy + 5], fill=YELLOW)

    base = GRAY if (rain or storm or snow) else (200, 200, 200)
    draw.ellipse([cx - 28, cy - 8, cx, cy + 20], fill=base)
    draw.ellipse([cx - 11, cy - 15, cx + 27, cy + 23], fill=base)
    draw.rounded_rectangle([cx - 32, cy + 4, cx + 30, cy + 20], radius=8, fill=base)

    if rain:
        for dx in (-10, 2, 14):
            draw.line([cx + dx, cy + 23, cx + dx - 3, cy + 33], fill=BLUE, width=4)
    if snow:
        for dx in (-10, 2, 14):
            draw.ellipse([cx + dx - 3, cy + 25, cx + dx + 3, cy + 31], fill=BLUE)
    if storm:
        draw.polygon([(cx - 2, cy + 24), (cx + 10, cy + 24),
                      (cx + 2, cy + 34), (cx + 12, cy + 34),
                      (cx - 6, cy + 46), (cx, cy + 34), (cx - 8, cy + 34)],
                     fill=YELLOW)


def render(days, now=None):
    now = now or dt.datetime.now()
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    f_title = load_font(32, "Bold")
    f_meta = load_font(17, "Regular")
    f_day = load_font(24, "Bold")
    f_date = load_font(17, "Regular")
    f_temp = load_font(20, "Regular")
    f_wind = load_font(54, "Bold")
    f_unit = load_font(20, "Medium")
    f_gust = load_font(19, "Medium")
    f_dir = load_font(21, "Medium")
    f_badge = load_font(22, "Bold")
    f_badge_s = load_font(21, "Bold")
    f_small = load_font(16, "Regular")

    # Kopfzeile
    draw_kite_mark(d, 34, 22, 1.0, BLACK, width=3)
    draw_tracked(d, (62, 46), "SHREDOGRAPH", f_title, BLACK, tracking=3)
    d.text((784, 28), "%s · %s" % (SPOT_NAME, SPOT_SUB), font=f_meta,
           fill=BLACK, anchor="rs")
    d.text((784, 50), "%s %s" % (WEEKDAYS_SHORT[now.weekday()], now.strftime("%H:%M")),
           font=f_meta, fill=BLACK, anchor="rs")
    d.line([16, 64, 784, 64], fill=BLACK, width=3)

    for i, day in enumerate(days):
        cx = 80 + i * 160
        color = COLORS[day["key"]]
        inactive = day["key"] == "muted"

        if i:
            d.line([i * 160, 82, i * 160, 388], fill=HAIRLINE, width=1)

        draw_tracked(d, (cx, 106), WEEKDAYS[day["date"].weekday()], f_day,
                     BLACK, tracking=2, anchor="ms")
        d.text((cx, 127), day["date"].strftime("%d.%m."), font=f_date,
               fill=BLACK, anchor="ms")

        draw_weather(d, cx, 156, day["code"])
        d.text((cx, 218), "%d°" % day["temp"], font=f_temp, fill=BLACK, anchor="ms")

        d.text((cx + 6, 272), str(day["wind"]), font=f_wind, fill=color, anchor="rs")
        d.text((cx + 12, 272), "kn", font=f_unit, fill=color, anchor="ls")
        d.text((cx, 298), "Böen %d" % day["gust"], font=f_gust, fill=color, anchor="ms")

        # Richtung: Pfeil und Kuerzel als Block zentriert
        label = compass(day["dir"])
        lw = d.textlength(label, font=f_dir)
        block = 24 + lw
        ax = cx - block / 2 + 11
        draw_arrow(d, ax, 322, day["dir"], BLACK)
        d.text((cx - block / 2 + 26, 329), label, font=f_dir, fill=BLACK, anchor="ls")

        # Kitegroesse
        bw = 124 if len(day["kite"]) > 5 else 104
        box = [cx - bw / 2, 346, cx + bw / 2, 384]
        font = f_badge_s if len(day["kite"]) > 4 else f_badge
        if inactive:
            d.rounded_rectangle(box, radius=19, outline=BLACK, width=2)
            d.text((cx, 372), day["kite"], font=font, fill=BLACK, anchor="ms")
        else:
            d.rounded_rectangle(box, radius=19, fill=color)
            d.text((cx, 372), day["kite"], font=font, fill=WHITE, anchor="ms")

    # Tagesverlauf
    d.line([16, 392, 784, 392], fill=HAIRLINE, width=1)
    draw_tracked(d, (16, 412), "TAGESVERLAUF %02d – %02d UHR" % (DAY_START, DAY_END),
                 f_small, BLACK, tracking=1)

    baseline, max_h, max_kn = 458, 40, 40
    for i, day in enumerate(days):
        color = COLORS[day["key"]] if day["key"] != "muted" else GRAY
        for j, val in enumerate(day["profile"][:8]):
            h = max(3, min(max_h, val / max_kn * max_h))
            x = 20 + i * 160 + j * 17
            d.rectangle([x, baseline - h, x + 12, baseline], fill=color)
    d.line([16, baseline, 784, baseline], fill=BLACK, width=1)

    d.text((16, 476), "Shredograph by Tzunamy :)",
           font=f_small, fill=BLACK, anchor="ls")
    return img


# --------------------------------------------------------------------------
# Ausgabe
# --------------------------------------------------------------------------


def to_spectra6(img):
    """Floyd-Steinberg-Dithering auf die sechs Panel-Farben."""
    pal = Image.new("P", (1, 1))
    flat = [v for rgb in SPECTRA6 for v in rgb]
    pal.putpalette(flat + [0, 0, 0] * (256 - len(SPECTRA6)))
    return img.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG)


def main():
    ap = argparse.ArgumentParser(description="Shredograph E-Ink-Renderer")
    ap.add_argument("--demo", action="store_true",
                    help="Beispieldaten statt Open-Meteo verwenden")
    ap.add_argument("--out", default=".", help="Zielverzeichnis")
    args = ap.parse_args()

    days = demo_days() if args.demo else aggregate(fetch_forecast())
    img = render(days)

    os.makedirs(args.out, exist_ok=True)
    preview = os.path.join(args.out, "shredograph_preview.png")
    png = os.path.join(args.out, "shredograph.png")
    bmp = os.path.join(args.out, "shredograph.bmp")

    dithered = to_spectra6(img).convert("RGB")
    img.save(preview)          # volle Farbtiefe, nur zum Anschauen
    dithered.save(png)         # das laedt das Display
    dithered.save(bmp)         # Alternative fuer Firmware, die BMP will
    print("geschrieben: %s, %s, %s" % (preview, png, bmp))


if __name__ == "__main__":
    main()
