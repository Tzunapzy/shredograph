#!/usr/bin/env python3
"""Tour-Display - Tagesnachricht, Foto und Etappendaten auf 800x480.

Wird vom GitHub-Workflow aufgerufen, sobald du ein Tagesupdate abschickst.
Liest den Inhalt des Formulars aus der Umgebungsvariable ISSUE_BODY.

    python3 tour.py --demo --out build
"""

import argparse
import datetime as dt
import io
import json
import os
import re
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------
# Konfiguration
# --------------------------------------------------------------------------

TOUR_NAME = "Camino Francés"
START_ORT = "Bilbao"
ZIEL_ORT = "Santiago"
TOUR_START = dt.date(2026, 9, 18)      # erster Tag der Tour
TOUR_TAGE = 12
STAND_DATEI = "tour_stand.json"        # merkt sich Gesamt-km zwischen den Tagen
LETZTES_DATEI = "letztes_update.json"  # damit das Bild auch ohne neue Nachricht
FOTO_DATEI = "letztes_foto.png"        # neu gebaut werden kann (Altersstempel)

W, H = 800, 480
BASIS = os.path.dirname(os.path.abspath(__file__))
FONT_SG = os.path.join(BASIS, "fonts", "SpaceGrotesk[wght].ttf")
FONT_HAND = os.path.join(BASIS, "fonts", "Caveat[wght].ttf")

SPECTRA6 = [(0, 0, 0), (255, 255, 255), (255, 243, 56),
            (191, 0, 0), (100, 64, 255), (67, 138, 28)]

BLACK, WHITE = (0, 0, 0), (255, 255, 255)
GRAU, HELLGRAU, LINIE = (110, 110, 110), (201, 201, 201), (220, 218, 211)
GELB, ROT, BLAU, GRUEN = (242, 194, 0), (192, 57, 43), (27, 78, 155), (30, 122, 60)

WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
              "Freitag", "Samstag", "Sonntag"]
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]

FOTO_BOX = (16, 68, 360, 318)          # links, oben, rechts, unten

# --------------------------------------------------------------------------


def font(pfad, size, weight=None):
    try:
        f = ImageFont.truetype(pfad, size)
        if weight:
            f.set_variation_by_name(weight)
        return f
    except (OSError, AttributeError):
        fb = "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"
        return ImageFont.truetype(fb % ("-Bold" if weight == "Bold" else ""), size)


# --------------------------------------------------------------------------
# Formular auslesen
# --------------------------------------------------------------------------


def parse_issue(body):
    """GitHub-Formulare kommen als Text mit ### Ueberschriften zurueck."""
    felder, aktuell = {}, None
    for zeile in body.replace("\r", "").split("\n"):
        if zeile.startswith("### "):
            aktuell = zeile[4:].strip().lower()
            felder[aktuell] = []
        elif aktuell:
            felder[aktuell].append(zeile)

    def hole(*namen):
        for name in namen:
            for k, v in felder.items():
                if name in k:
                    text = "\n".join(v).strip()
                    if text and text != "_No response_":
                        return text
        return ""

    # Angehaengte Bilder stehen als Markdown irgendwo im Text
    treffer = re.findall(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", body)

    return {
        "nachricht": hole("nachricht"),
        "ort": hole("wo bist du", "ort"),
        "km": hole("kilometer"),
        "hm": hole("höhenmeter", "hoehenmeter"),
        "morgen": hole("morgen geht"),
        "wann": hole("wann").lower(),
        "foto_url": treffer[0] if treffer else None,
    }


# --------------------------------------------------------------------------
# Daten holen
# --------------------------------------------------------------------------


def geocode(ort):
    p = {"name": ort, "count": 1, "language": "de", "format": "json"}
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(p)
    with urllib.request.urlopen(url, timeout=20) as r:
        d = json.load(r)
    if not d.get("results"):
        return None
    t = d["results"][0]
    return {"lat": t["latitude"], "lon": t["longitude"],
            "hoehe": t.get("elevation"), "region": t.get("admin1", ""),
            "name": t["name"]}


def wetter_heute(lat, lon):
    """Morgens interessiert die Aussicht auf den Tag, nicht der Istwert."""
    p = {"latitude": lat, "longitude": lon,
         "daily": ("temperature_2m_max,temperature_2m_min,"
                   "precipitation_probability_max,weather_code"),
         "timezone": "auto", "forecast_days": 1}
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(p)
    with urllib.request.urlopen(url, timeout=20) as r:
        dd = json.load(r)["daily"]
    return {"max": round(dd["temperature_2m_max"][0]),
            "min": round(dd["temperature_2m_min"][0]),
            "regen": dd["precipitation_probability_max"][0] or 0,
            "code": dd["weather_code"][0]}


def wetter(lat, lon):
    p = {"latitude": lat, "longitude": lon,
         "current": "temperature_2m,apparent_temperature,weather_code",
         "timezone": "auto"}
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(p)
    with urllib.request.urlopen(url, timeout=20) as r:
        c = json.load(r)["current"]
    return {"temp": round(c["temperature_2m"]),
            "gefuehlt": round(c["apparent_temperature"]),
            "code": c["weather_code"]}


def lade_foto(url):
    req = urllib.request.Request(url, headers={"User-Agent": "tour-display"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGB")


def stand_lesen():
    if os.path.exists(STAND_DATEI):
        return json.load(open(STAND_DATEI, encoding="utf-8"))
    return {"km_gesamt": 0, "hm_gesamt": 0, "letzter_tag": None}


def stand_schreiben(stand):
    json.dump(stand, open(STAND_DATEI, "w", encoding="utf-8"))


def letztes_schreiben(d, foto):
    ablage = {k: v for k, v in d.items() if k != "foto"}
    ablage["datum"] = d["datum"].isoformat()
    ablage["hat_foto"] = foto is not None
    json.dump(ablage, open(LETZTES_DATEI, "w", encoding="utf-8"), ensure_ascii=False)
    if foto is not None:
        foto_einpassen(foto, FOTO_BOX).save(FOTO_DATEI)


def letztes_lesen():
    """Fuer die geplanten Laeufe ohne neue Nachricht: alte Inhalte, neuer Stempel."""
    if not os.path.exists(LETZTES_DATEI):
        return None
    d = json.load(open(LETZTES_DATEI, encoding="utf-8"))
    geschrieben = dt.date.fromisoformat(d["datum"])
    heute = dt.date.today()
    d["datum"] = heute
    d["alter_tage"] = (heute - geschrieben).days
    d["tag"] = max(1, (heute - TOUR_START).days + 1)
    # Die Variante richtet sich nach der Tageszeit des Laufs, nicht nach der
    # der alten Nachricht - morgens soll morgens stehen.
    d["variante"] = "morgen" if dt.datetime.now().hour < 12 else "abend"
    # Wetter frisch holen: gespeicherte Werte sind alt und passen ausserdem
    # nicht zur Variante (morgens Vorhersage, abends Istwert).
    d["wetter"] = None
    if d.get("lat") is not None:
        try:
            d["wetter"] = (wetter_heute(d["lat"], d["lon"]) if d["variante"] == "morgen"
                           else wetter(d["lat"], d["lon"]))
        except Exception:
            d["wetter"] = None

    d["foto"] = None
    if d.get("hat_foto") and os.path.exists(FOTO_DATEI):
        try:
            d["foto"] = Image.open(FOTO_DATEI).convert("RGB")
        except Exception:
            d["foto"] = None
    return d


# --------------------------------------------------------------------------
# Zeichnen
# --------------------------------------------------------------------------


def foto_einpassen(img, box):
    """Mittig zuschneiden und auf die Box bringen - nie verzerren."""
    bx, by, bx2, by2 = box
    zw, zh = bx2 - bx, by2 - by
    verhaeltnis = max(zw / img.width, zh / img.height)
    neu = img.resize((max(1, round(img.width * verhaeltnis)),
                      max(1, round(img.height * verhaeltnis))), Image.LANCZOS)
    links = (neu.width - zw) // 2
    oben = (neu.height - zh) // 2
    return neu.crop((links, oben, links + zw, oben + zh))


def wetter_symbol(d, cx, cy, code):
    if code in (0, 1):
        d.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], fill=GELB)
        for a, b, c, e in [(0, -18, 0, -23), (0, 18, 0, 23),
                           (-18, 0, -23, 0), (18, 0, 23, 0)]:
            d.line([cx + a, cy + b, cx + c, cy + e], fill=GELB, width=4)
        return
    if code == 2:
        d.ellipse([cx - 2, cy - 14, cx + 22, cy + 10], fill=GELB)
    regen = code not in (0, 1, 2, 3, 45, 48)
    farbe = (140, 140, 140) if regen else HELLGRAU
    d.ellipse([cx - 20, cy - 4, cx, cy + 16], fill=farbe)
    d.ellipse([cx - 10, cy - 10, cx + 16, cy + 16], fill=farbe)
    d.rounded_rectangle([cx - 22, cy + 4, cx + 16, cy + 16], radius=6, fill=farbe)
    if regen:
        for dx in (-10, 0, 10):
            d.line([cx + dx, cy + 20, cx + dx - 3, cy + 28], fill=BLAU, width=4)


def umbrechen(d, text, f, max_breite, max_zeilen):
    zeilen, aktuell = [], ""
    for wort in text.split():
        probe = (aktuell + " " + wort).strip()
        if d.textlength(probe, font=f) <= max_breite:
            aktuell = probe
        else:
            zeilen.append(aktuell)
            aktuell = wort
            if len(zeilen) == max_zeilen:
                break
    if aktuell and len(zeilen) < max_zeilen:
        zeilen.append(aktuell)
    return zeilen[:max_zeilen]


def render(daten):
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    f_tag = font(FONT_SG, 25, "Bold")
    f_tour = font(FONT_SG, 18)
    f_meta = font(FONT_SG, 15)
    f_label = font(FONT_SG, 12)
    f_gross = font(FONT_SG, 21, "Bold")
    f_mittel = font(FONT_SG, 19, "Bold")
    f_klein = font(FONT_SG, 15)
    f_zahl = font(FONT_SG, 26, "Bold")
    f_temp = font(FONT_SG, 24, "Bold")
    f_hand = font(FONT_HAND, 33, "Bold")

    heute = daten["datum"]
    tag = daten["tag"]

    # Kopf
    morgens = daten["variante"] == "morgen"
    if morgens:
        f_gruss = font(FONT_SG, 44, "Bold")
        d.text((16, 44), "GUTEN MORGEN", font=f_gruss, fill=BLACK, anchor="ls")
        d.text((402, 44), TOUR_NAME, font=f_tour, fill=GRAU, anchor="ls")
    else:
        d.ellipse([19, 26, 35, 42], outline=GRUEN, width=3)
        d.ellipse([43, 26, 59, 42], outline=GRUEN, width=3)
        d.line([27, 34, 39, 22, 48, 34], fill=GRUEN, width=3, joint="curve")
        d.line([35, 22, 43, 22], fill=GRUEN, width=3)
        d.text((72, 36), "TAG %d VON %d" % (tag, TOUR_TAGE), font=f_tag,
               fill=BLACK, anchor="ls")
        d.text((284, 36), TOUR_NAME, font=f_tour, fill=GRAU, anchor="ls")
    d.text((784, 24), "%s, %d. %s" % (WOCHENTAGE[heute.weekday()], heute.day,
                                      MONATE[heute.month - 1]),
           font=f_meta, fill=GRAU, anchor="rs")
    alter = daten["alter_tage"]
    if alter == 0:
        stempel, farbe = "von heute " + daten["uhrzeit"], GRUEN
    elif alter == 1:
        stempel, farbe = "von gestern", GRAU
    else:
        stempel, farbe = "von vor %d Tagen" % alter, ROT
    d.text((784, 42), stempel, font=f_meta, fill=farbe, anchor="rs")
    d.line([16, 52, 784, 52], fill=BLACK, width=3)

    # Foto
    if morgens:
        pass  # Layout bleibt gleich, nur die Kopfzeile ist hoeher gewichtet
    if daten["foto"] is not None:
        img.paste(foto_einpassen(daten["foto"], FOTO_BOX), (FOTO_BOX[0], FOTO_BOX[1]))
    else:
        d.rectangle(FOTO_BOX, fill=(246, 246, 244))
        d.text(((FOTO_BOX[0] + FOTO_BOX[2]) / 2, (FOTO_BOX[1] + FOTO_BOX[3]) / 2),
               "heute ohne Foto", font=f_klein, fill=HELLGRAU, anchor="mm")
    d.rectangle(FOTO_BOX, outline=BLACK, width=2)

    # Nachricht
    zeilen = umbrechen(d, daten["nachricht"], f_hand, 400, 6)
    for i, z in enumerate(zeilen):
        d.text((384, 102 + i * 38), z, font=f_hand, fill=BLACK, anchor="ls")

    # Infozeile
    d.line([16, 336, 784, 336], fill=LINIE, width=1)

    d.text((16, 358), "GERADE HIER", font=f_label, fill=GRAU, anchor="ls")
    d.text((16, 384), daten["ort"], font=f_gross, fill=BLACK, anchor="ls")
    d.text((16, 404), daten["ort_zusatz"], font=f_klein, fill=GRAU, anchor="ls")

    d.line([210, 346, 210, 410], fill=LINIE, width=1)
    d.text((238, 358), "WETTER HEUTE" if morgens else "WETTER DORT",
           font=f_label, fill=GRAU, anchor="ls")
    w = daten["wetter"]
    if w and morgens and "max" in w:
        wetter_symbol(d, 252, 382, w["code"])
        d.text((290, 390), "%d°" % w["max"], font=f_temp, fill=BLACK, anchor="ls")
        d.text((290 + d.textlength("%d°" % w["max"], font=f_temp) + 6, 390),
               "/ %d°" % w["min"], font=f_klein, fill=GRAU, anchor="ls")
        d.text((238, 408), "Regen %d %%" % w["regen"], font=f_klein,
               fill=BLAU if w["regen"] >= 40 else GRAU, anchor="ls")
    elif w and "temp" in w:
        wetter_symbol(d, 252, 382, w["code"])
        d.text((290, 390), "%d°" % w["temp"], font=f_temp, fill=BLACK, anchor="ls")
        d.text((238, 408), "gefühlt %d°" % w["gefuehlt"],
               font=f_klein, fill=GRAU, anchor="ls")

    d.line([380, 346, 380, 410], fill=LINIE, width=1)
    d.line([616, 346, 616, 410], fill=LINIE, width=1)

    if morgens:
        d.text((408, 358), "TOUR", font=f_label, fill=GRAU, anchor="ls")
        d.text((408, 388), "Tag %d" % tag, font=f_zahl, fill=BLACK, anchor="ls")
        d.text((408 + d.textlength("Tag %d" % tag, font=f_zahl) + 10, 388),
               "von %d" % TOUR_TAGE, font=f_klein, fill=GRAU, anchor="ls")
        d.text((408, 408), "%d Tage noch" % max(0, TOUR_TAGE - tag),
               font=f_klein, fill=GRAU, anchor="ls")

        d.text((644, 358), "BISHER GESAMT", font=f_label, fill=GRAU, anchor="ls")
        d.text((644, 388), "%d" % daten["km_gesamt"], font=f_zahl, fill=BLACK, anchor="ls")
        d.text((644 + d.textlength("%d" % daten["km_gesamt"], font=f_zahl) + 8, 388),
               "km", font=f_klein, fill=GRAU, anchor="ls")
        if daten.get("hm_gesamt"):
            d.text((644, 408), "%d hm" % daten["hm_gesamt"],
                   font=f_klein, fill=GRAU, anchor="ls")
    else:
        d.text((408, 358), "HEUTE", font=f_label, fill=GRAU, anchor="ls")
        x = 408
        if daten["km"]:
            d.text((x, 388), daten["km"], font=f_zahl, fill=BLACK, anchor="ls")
            x += d.textlength(daten["km"], font=f_zahl) + 8
            d.text((x, 388), "km", font=f_klein, fill=GRAU, anchor="ls")
            x += 38
        if daten["hm"]:
            d.text((x, 388), daten["hm"], font=f_zahl, fill=BLACK, anchor="ls")
            x += d.textlength(daten["hm"], font=f_zahl) + 8
            d.text((x, 388), "hm", font=f_klein, fill=GRAU, anchor="ls")
        d.text((408, 408), "gesamt %d km" % daten["km_gesamt"],
               font=f_klein, fill=GRAU, anchor="ls")

        d.text((644, 358), "MORGEN", font=f_label, fill=GRAU, anchor="ls")
        d.text((644, 384), daten["morgen"] or "offen", font=f_mittel, fill=BLACK, anchor="ls")

    # Fortschritt
    d.line([16, 424, 784, 424], fill=BLACK, width=2)
    d.rounded_rectangle([16, 438, 784, 452], radius=7, fill=LINIE)
    anteil = min(1.0, tag / TOUR_TAGE)
    breite = max(14, round(768 * anteil))
    d.rounded_rectangle([16, 438, 16 + breite, 452], radius=7, fill=GRUEN)
    d.ellipse([16 + breite - 11, 434, 16 + breite + 11, 456], fill=ROT)
    d.text((16, 474), START_ORT, font=f_klein, fill=GRUEN, anchor="ls")
    rest = max(0, TOUR_TAGE - tag)
    d.text((784, 474), "%s · noch %d Tage" % (ZIEL_ORT, rest),
           font=f_klein, fill=GRAU, anchor="rs")
    return img


# --------------------------------------------------------------------------


def to_spectra6(img):
    pal = Image.new("P", (1, 1))
    flat = [v for rgb in SPECTRA6 for v in rgb]
    pal.putpalette(flat + [0, 0, 0] * (256 - len(SPECTRA6)))
    return img.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG)


def demo(variante="abend"):
    w = ({"max": 21, "min": 9, "regen": 30, "code": 2} if variante == "morgen"
         else {"temp": 19, "gefuehlt": 17, "code": 2})
    return {
        "variante": variante,
        "datum": dt.date.today(), "tag": 4, "alter_tage": 0,
        "uhrzeit": "07:20" if variante == "morgen" else "19:40",
        "nachricht": (("Gut geschlafen, der Bach war lauter als gedacht. "
                       "Kaffee kocht schon, heute geht es früh los, damit ich "
                       "vor der Hitze oben bin. Denkt an mich beim Frühstück!")
                      if variante == "morgen" else
                      ("Heute früh im Nebel über den Pass, oben nur Schafe. "
                       "Dann 20 km Abfahrt in die Sonne, schönster Moment der Tour. "
                       "Beine platt, Kopf leer und glücklich. Zelt steht am Bach. "
                       "Vermisse euch, bis morgen!")),
        "ort": "Potes", "ort_zusatz": "Kantabrien · 291 m", "wetter": w,
        "km": "78", "hm": "1450", "km_gesamt": 312, "hm_gesamt": 6800,
        "morgen": "Fuente Dé", "foto": None,
    }


def aus_issue():
    body = os.environ.get("ISSUE_BODY", "").strip()
    if not body:
        # Geplanter Lauf ohne neue Nachricht: altes Bild mit neuem Stempel
        alt = letztes_lesen()
        if alt:
            return alt
        raise SystemExit("Keine Nachricht und kein gespeichertes Update vorhanden.")
    f = parse_issue(body)
    jetzt = dt.datetime.now()

    ort_zusatz, w = "", None
    treffer = geocode(f["ort"]) if f["ort"] else None
    if treffer:
        teile = [t for t in [treffer["region"]] if t]
        if treffer.get("hoehe"):
            teile.append("%d m" % round(treffer["hoehe"]))
        ort_zusatz = " · ".join(teile)
        try:
            w = (wetter_heute(treffer["lat"], treffer["lon"])
                 if (jetzt.hour < 12) else wetter(treffer["lat"], treffer["lon"]))
        except Exception:
            w = None

    foto = None
    if f["foto_url"]:
        try:
            foto = lade_foto(f["foto_url"])
        except Exception:
            foto = None

    # Vor 12 Uhr ist es die Morgenvariante - im Formular uebersteuerbar
    if "abend" in f["wann"]:
        variante = "abend"
    elif "morgen" in f["wann"]:
        variante = "morgen"
    else:
        variante = "morgen" if jetzt.hour < 12 else "abend"

    stand = stand_lesen()
    heute_iso = jetzt.date().isoformat()
    if variante == "abend" and f["km"] and stand.get("letzter_tag") != heute_iso:
        try:
            stand["km_gesamt"] += int(re.sub(r"\D", "", f["km"]) or 0)
            stand["hm_gesamt"] += int(re.sub(r"\D", "", f["hm"]) or 0)
        except ValueError:
            pass
        stand["letzter_tag"] = heute_iso
        stand_schreiben(stand)

    ergebnis = {
        "variante": variante,
        "datum": jetzt.date(),
        "tag": max(1, (jetzt.date() - TOUR_START).days + 1),
        "alter_tage": 0,
        "uhrzeit": jetzt.strftime("%H:%M"),
        "nachricht": f["nachricht"] or "Heute keine Nachricht.",
        "ort": treffer["name"] if treffer else (f["ort"] or "unterwegs"),
        "ort_zusatz": ort_zusatz,
        "wetter": w, "km": f["km"], "hm": f["hm"],
        "lat": treffer["lat"] if treffer else None,
        "lon": treffer["lon"] if treffer else None,
        "km_gesamt": stand["km_gesamt"], "hm_gesamt": stand.get("hm_gesamt", 0),
        "morgen": f["morgen"], "foto": foto,
    }
    letztes_schreiben(ergebnis, foto)
    return ergebnis


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--variante", choices=["morgen", "abend"], default="abend")
    ap.add_argument("--out", default=".")
    args = ap.parse_args()

    daten = demo(args.variante) if args.demo else aus_issue()
    img = render(daten)
    os.makedirs(args.out, exist_ok=True)
    img.save(os.path.join(args.out, "tour_preview.png"))
    to_spectra6(img).convert("RGB").save(os.path.join(args.out, "shredograph.png"))
    print("geschrieben nach", args.out)


if __name__ == "__main__":
    main()
