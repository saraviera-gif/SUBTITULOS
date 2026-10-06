#!/usr/bin/env python3
"""
Revisión de subtítulos quemados en vídeos largos (español).

Uso:
    python revisar.py "<enlace de Dropbox>"                 # pasos 1-3 -> revision_pendiente.json
    python revisar.py "<enlace>" --prueba 3                  # solo los 3 primeros minutos
    python revisar.py "<enlace>" --zona 0.80,0.96            # fijar la franja de subtítulos
    python revisar.py "<enlace>" --informe                   # genera HTML + PDF a partir de fallos.json

Cada paso guarda su resultado en trabajos/<vídeo>/ y se reutiliza si se vuelve a ejecutar.
Cuando ya existe fallos.json (lo escribe Claude Code tras revisar), el informe se genera solo.
"""

import argparse
import base64
import bisect
import difflib
import html
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

import numpy as np
import requests
from PIL import Image, ImageDraw
from tqdm import tqdm

RAIZ = Path(__file__).resolve().parent
TRABAJOS = RAIZ / "trabajos"
TAM_BLOQUE = 40
TIPOS = {
    "ortografia": "Ortografía",
    "fidelidad": "Fidelidad al audio",
    "sincronia": "Sincronía",
}


# --------------------------------------------------------------------------- utilidades

def info(msg):
    print(f"\n▶ {msg}", flush=True)


def aviso(msg):
    print(f"\n⚠️  {msg}", flush=True)


def ffmpeg_bin():
    sistema = shutil.which("ffmpeg")
    if sistema:
        return sistema
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


FFMPEG = ffmpeg_bin()


def guardar_json(ruta, datos):
    tmp = Path(str(ruta) + ".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ruta)


def leer_json(ruta):
    return json.loads(Path(ruta).read_text(encoding="utf-8"))


def tc(seg, decimales=False):
    """Segundos -> 'm:ss' o 'h:mm:ss'."""
    seg = max(0.0, seg)
    h, resto = divmod(seg, 3600)
    m, s = divmod(resto, 60)
    s_txt = f"{s:04.1f}" if decimales else f"{int(s):02d}"
    return f"{int(h)}:{int(m):02d}:{s_txt}" if h >= 1 else f"{int(m)}:{s_txt}"


def normalizar(texto):
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9ñ ]+", " ", t).split()


# --------------------------------------------------------------------------- 1. Dropbox

def con_param(url, **params):
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query) if k not in ("dl", "raw")]
    q += list(params.items())
    return urlunparse(p._replace(query=urlencode(q)))


def enlaces_dropbox(url):
    url = url.strip()
    if "dropbox.com" not in url:
        aviso("El enlace no parece de Dropbox; lo uso tal cual.")
        return url, url
    return con_param(url, dl="1"), con_param(url, raw="1")


def nombre_desde_url(url):
    nombre = unquote(Path(urlparse(url).path).name) or "video.mp4"
    return re.sub(r"[^\w.\- ]+", "_", nombre)


def descargar(url_dl, destino):
    if destino.exists():
        print(f"  Ya descargado: {destino.name} ({destino.stat().st_size / 1e9:.2f} GB)")
        return
    parcial = Path(str(destino) + ".part")
    ya = parcial.stat().st_size if parcial.exists() else 0
    cabeceras = {"Range": f"bytes={ya}-"} if ya else {}
    with requests.get(url_dl, stream=True, allow_redirects=True, headers=cabeceras, timeout=60) as r:
        if r.status_code == 416:  # ya completo
            parcial.replace(destino)
            return
        r.raise_for_status()
        if ya and r.status_code != 206:
            aviso("El servidor no permite reanudar; empiezo de cero.")
            ya = 0
        if "text/html" in r.headers.get("Content-Type", ""):
            sys.exit("✖ Dropbox ha devuelto una página web en vez del vídeo. "
                     "¿El enlace es público y apunta a un archivo (no a una carpeta)?")
        total = int(r.headers.get("Content-Length", 0)) + ya
        with open(parcial, "ab" if ya else "wb") as f, tqdm(
            total=total or None, initial=ya, unit="B", unit_scale=True, unit_divisor=1024,
            desc="  Descargando",
        ) as barra:
            for trozo in r.iter_content(chunk_size=4 * 1024 * 1024):
                f.write(trozo)
                barra.update(len(trozo))
    parcial.replace(destino)


def descargar_prueba(url_dl, destino, minutos):
    """Copia solo los primeros N minutos directamente desde el enlace (sin bajar el archivo entero)."""
    if destino.exists():
        print(f"  Ya descargado: {destino.name}")
        return
    tmp = destino.with_suffix(".tmp" + destino.suffix)
    print(f"  Copiando los primeros {minutos} min desde Dropbox…")
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-stats", "-y", "-i", url_dl,
           "-t", str(minutos * 60), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", str(tmp)]
    if subprocess.run(cmd).returncode != 0:
        sys.exit("✖ No se pudo copiar el fragmento de prueba.")
    tmp.replace(destino)


def datos_video(ruta):
    err = subprocess.run([FFMPEG, "-hide_banner", "-i", str(ruta)], capture_output=True, text=True).stderr
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    res = re.search(r"Video: .*?(\d{2,5})x(\d{2,5})", err)
    fps = re.search(r"([\d.]+) fps", err)
    if not (dur and res):
        sys.exit(f"✖ No pude leer el vídeo {ruta}:\n{err[-800:]}")
    h, m, s = dur.groups()
    return {
        "duracion": int(h) * 3600 + int(m) * 60 + float(s),
        "ancho": int(res.group(1)),
        "alto": int(res.group(2)),
        "fps": float(fps.group(1)) if fps else 25.0,
    }


# --------------------------------------------------------------------------- 2. Audio

def extraer_audio(video, wav):
    if wav.exists():
        print("  Audio ya extraído.")
        return
    tmp = wav.with_suffix(".tmp.wav")
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-stats", "-y", "-i", str(video),
                    "-vn", "-ac", "1", "-ar", "16000", str(tmp)], check=True)
    tmp.replace(wav)


def hay_cuda():
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def es_apple_silicon():
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def elegir_motor(pedido):
    if pedido == "auto":
        if hay_cuda():
            return "faster-whisper"
        aviso("Este ordenador no tiene GPU NVIDIA (CUDA): faster-whisper iría por CPU y large-v3 "
              "tardaría aproximadamente lo que dura el vídeo, o más.")
        if es_apple_silicon():
            print("   Uso mlx-whisper (mismo modelo large-v3, en la GPU de Apple, gratis y local).\n"
                  "   Alternativas: --motor faster-whisper (CPU) o --motor openai (API de pago).")
            return "mlx"
        return preguntar_motor()
    if pedido == "faster-whisper" and not hay_cuda():
        aviso("No hay GPU NVIDIA: faster-whisper irá por CPU y será lento en vídeos largos.")
        return preguntar_motor()
    return pedido


def preguntar_motor():
    opciones = [("faster-whisper", "Seguir con faster-whisper en CPU (gratis, lento)")]
    if es_apple_silicon():
        opciones.insert(0, ("mlx", "mlx-whisper en la GPU de Apple (gratis, rápido)"))
    opciones.append(("openai", "API de transcripción de OpenAI (de pago, ~0,006 $/min, necesita OPENAI_API_KEY)"))
    if not sys.stdin.isatty():
        print(f"   Sin terminal interactiva: uso {opciones[0][0]}.")
        return opciones[0][0]
    for i, (_, txt) in enumerate(opciones, 1):
        print(f"   [{i}] {txt}")
    resp = input("   Elige opción [1]: ").strip() or "1"
    try:
        return opciones[int(resp) - 1][0]
    except (ValueError, IndexError):
        return opciones[0][0]


def transcribir(wav, salida, motor, duracion):
    if salida.exists():
        print(f"  Transcripción ya hecha ({leer_json(salida)['motor']}).")
        return
    motor = elegir_motor(motor)
    info(f"Transcribiendo con {motor} (large-v3, español, marcas por palabra)…")
    t0 = time.time()
    if motor == "mlx":
        segmentos = transcribir_mlx(wav)
    elif motor == "faster-whisper":
        segmentos = transcribir_faster(wav, duracion)
    elif motor == "openai":
        segmentos = transcribir_openai(wav, salida.parent)
    else:
        sys.exit(f"✖ Motor desconocido: {motor}")
    guardar_json(salida, {"motor": motor, "segmentos": segmentos})
    print(f"  Hecho en {tc(time.time() - t0)} ({len(segmentos)} segmentos).")


def cargar_wav(wav):
    import wave
    with wave.open(str(wav)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0


def transcribir_mlx(wav):
    import mlx_whisper
    r = mlx_whisper.transcribe(
        cargar_wav(wav), path_or_hf_repo="mlx-community/whisper-large-v3-mlx", language="es",
        word_timestamps=True, condition_on_previous_text=False, verbose=False,
    )
    return [{
        "inicio": s["start"], "fin": s["end"], "texto": s["text"].strip(),
        "palabras": [[w["word"].strip(), round(w["start"], 2), round(w["end"], 2)] for w in s.get("words", [])],
    } for s in r["segments"]]


def transcribir_faster(wav, duracion):
    from faster_whisper import WhisperModel
    dispositivo = "cuda" if hay_cuda() else "cpu"
    modelo = WhisperModel("large-v3", device=dispositivo,
                          compute_type="float16" if dispositivo == "cuda" else "int8")
    segs, _ = modelo.transcribe(str(wav), language="es", word_timestamps=True, vad_filter=True,
                                condition_on_previous_text=False)
    salida = []
    with tqdm(total=round(duracion), unit="s", desc="  Transcribiendo") as barra:
        for s in segs:
            salida.append({
                "inicio": s.start, "fin": s.end, "texto": s.text.strip(),
                "palabras": [[w.word.strip(), round(w.start, 2), round(w.end, 2)] for w in (s.words or [])],
            })
            barra.update(round(s.end) - barra.n)
    return salida


def transcribir_openai(wav, carpeta):
    from openai import OpenAI
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("✖ Falta la variable OPENAI_API_KEY.")
    cliente = OpenAI()
    trozos = carpeta / "audio_trozos"
    trozos.mkdir(exist_ok=True)
    lista = trozos / "lista.csv"
    if not lista.exists():  # trozos de 10 min en mp3 (< 25 MB, límite de la API)
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
                        "-f", "segment", "-segment_time", "600", "-segment_list", str(lista),
                        "-ac", "1", "-b:a", "64k", str(trozos / "trozo_%03d.mp3")], check=True)
    salida = []
    filas = [l.split(",") for l in lista.read_text().splitlines() if l.strip()]
    for nombre, ini, _fin in tqdm(filas, desc="  Enviando a OpenAI"):
        off = float(ini)
        with open(trozos / nombre, "rb") as f:
            r = cliente.audio.transcriptions.create(
                model="whisper-1", file=f, language="es", response_format="verbose_json",
                timestamp_granularities=["word", "segment"])
        palabras = [[w.word, round(w.start + off, 2), round(w.end + off, 2)] for w in (r.words or [])]
        for s in r.segments or []:
            a, b = s.start + off, s.end + off
            salida.append({"inicio": a, "fin": b, "texto": s.text.strip(),
                           "palabras": [p for p in palabras if a - 0.01 <= p[1] < b]})
    return salida


# --------------------------------------------------------------------------- 3. Subtítulos (OCR)

_LECTOR = None


def lector_ocr():
    global _LECTOR
    if _LECTOR is None:
        import warnings
        warnings.filterwarnings("ignore")
        import easyocr
        print("  Cargando EasyOCR (español)…")
        _LECTOR = easyocr.Reader(["es"], gpu=True, verbose=False)
    return _LECTOR


def fotograma(video, t, ancho=None):
    vf = ["-vf", f"scale={ancho}:-2"] if ancho else []
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
                        "-frames:v", "1", *vf, "-f", "image2pipe", "-vcodec", "png", "-"],
                       capture_output=True)
    return Image.open(io.BytesIO(r.stdout)).convert("RGB") if r.stdout else None


def es_texto_blanco(arr):
    return (arr.min(axis=2) > 160) & ((arr.max(axis=2).astype(int) - arr.min(axis=2)) < 60)


def parse_zona(txt, alto):
    a, b = (float(x) for x in txt.split(","))
    if a > 1 or b > 1:  # en píxeles
        a, b = a / alto, b / alto
    if not 0 <= a < b <= 1:
        sys.exit("✖ --zona debe ser 'inicio,fin' (fracciones 0-1 de la altura, o píxeles).")
    return a, b


def detectar_zona(video, datos, carpeta, zona_manual):
    ruta = carpeta / "zona.json"
    if zona_manual:
        y0, y1 = parse_zona(zona_manual, datos["alto"])
        previa = leer_json(ruta) if ruta.exists() else {}
        zona = {"y0": round(y0, 4), "y1": round(y1, 4), "modo": previa.get("modo", "gris"), "manual": True}
        if not previa:
            zona["modo"] = modo_cambio_en_zona(video, datos, y0, y1)
        guardar_json(ruta, zona)
        dibujar_zona(video, datos, zona, carpeta)
        return zona
    if ruta.exists():
        zona = leer_json(ruta)
        print(f"  Zona guardada: {zona['y0']:.3f}–{zona['y1']:.3f} de la altura ({zona['modo']}).")
        return zona

    print("  Buscando subtítulos en 40 fotogramas de muestra…")
    lector = lector_ocr()
    dur = datos["duracion"]
    tops, bottoms, fracs_blanco, mejor = [], [], [], None
    for i in tqdm(range(40), desc="  Muestras"):
        t = dur * (0.03 + 0.94 * i / 39)
        img = fotograma(video, t, ancho=1280)
        if img is None:
            continue
        arr = np.asarray(img)
        h = arr.shape[0]
        y_ini = h // 2
        encontrados = 0
        for l in lineas_centradas(lector.readtext(arr[y_ini:]), arr.shape[1], conf_min=0.3):
            if len(re.sub(r"\W", "", l["texto"])) < 2:
                continue
            top, bot = l["y0"] + y_ini, l["y1"] + y_ini
            tops.append(top / h)
            bottoms.append(bot / h)
            region = arr[int(top):int(bot), int(l["x0"]):int(l["x1"])]
            if region.size:
                fracs_blanco.append(es_texto_blanco(region).mean())
            encontrados += 1
        if encontrados and (mejor is None or encontrados > mejor[1]):
            mejor = (t, encontrados)

    if not tops:
        aviso("No encontré texto en las muestras. Uso la franja por defecto 0.72–0.96. "
              "Ajústala con --zona si no es correcta.")
        zona = {"y0": 0.72, "y1": 0.96, "modo": "gris", "manual": False}
    else:
        tops, bottoms = np.array(tops), np.array(bottoms)
        alto_linea = float(np.median(bottoms - tops))
        med_bot = float(np.median(bottoms))
        # reservar sitio para dos líneas de texto grande (frases animadas) aunque las muestras no las tengan
        y0 = min(float(tops.min()), med_bot - 7 * alto_linea) - 0.3 * alto_linea
        y1 = float(bottoms.max()) + 0.4 * alto_linea
        modo = "mascara" if fracs_blanco and np.median(fracs_blanco) > 0.04 else "gris"
        zona = {"y0": round(max(0.0, y0), 4), "y1": round(min(1.0, y1), 4), "modo": modo,
                "manual": False, "muestra_t": mejor[0] if mejor else None}
    guardar_json(ruta, zona)
    dibujar_zona(video, datos, zona, carpeta)
    print(f"  Zona detectada: {zona['y0']:.3f}–{zona['y1']:.3f} de la altura "
          f"(px {round(zona['y0'] * datos['alto'])}–{round(zona['y1'] * datos['alto'])}). "
          f"Vista previa: {carpeta / 'zona_preview.jpg'}")
    print("  Si no es correcta, repite con --zona INICIO,FIN (p. ej. --zona 0.80,0.96).")
    return zona


def modo_cambio_en_zona(video, datos, y0, y1):
    lector = lector_ocr()
    fracs = []
    for i in range(8):
        img = fotograma(video, datos["duracion"] * (0.1 + 0.8 * i / 7), ancho=1280)
        if img is None:
            continue
        arr = np.asarray(img)
        franja = arr[int(y0 * arr.shape[0]):int(y1 * arr.shape[0])]
        for l in lineas_centradas(lector.readtext(franja), franja.shape[1], conf_min=0.3):
            reg = franja[int(l["y0"]):int(l["y1"]), int(l["x0"]):int(l["x1"])]
            if reg.size:
                fracs.append(es_texto_blanco(reg).mean())
    return "mascara" if fracs and np.median(fracs) > 0.04 else "gris"


def dibujar_zona(video, datos, zona, carpeta):
    img = fotograma(video, zona.get("muestra_t") or datos["duracion"] * 0.3, ancho=1280)
    if img is None:
        return
    d = ImageDraw.Draw(img)
    h = img.height
    d.rectangle([2, zona["y0"] * h, img.width - 3, zona["y1"] * h], outline=(255, 0, 0), width=4)
    img.save(carpeta / "zona_preview.jpg", quality=85)


class Firma:
    """Representación reducida de la franja para saber si el subtítulo ha cambiado."""

    def __init__(self, arr, modo):
        h, w = arr.shape[0] // 4 * 4, arr.shape[1] // 4 * 4
        a = arr[:h, :w]
        self.modo = modo
        if modo == "mascara":
            m = es_texto_blanco(a).reshape(h // 4, 4, w // 4, 4).mean(axis=(1, 3))
            self.v = m > 0.2
            self.vacia = self.v.sum() < 12
        else:
            g = a.mean(axis=2).reshape(h // 4, 4, w // 4, 4).mean(axis=(1, 3))
            self.v = g
            self.vacia = False

    def distinta(self, otra):
        if otra is None:
            return True
        if self.modo == "mascara":
            if self.vacia and otra.vacia:
                return False
            union = (self.v | otra.v).sum()
            return (self.v ^ otra.v).sum() / max(union, 20) > 0.06
        return float(np.abs(self.v - otra.v).mean()) > 10


def lineas_centradas(resultados, ancho, conf_min=0.2):
    """Agrupa las cajas de EasyOCR en líneas y se queda con las centradas (los subtítulos).
    Descarta grafismos alineados a izquierda o derecha (listas, rótulos, logos)."""
    cajas = []
    for caja, texto, conf in resultados:
        if conf < conf_min or not texto.strip():
            continue
        xs, ys = [p[0] for p in caja], [p[1] for p in caja]
        cajas.append({"x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
                      "yc": (min(ys) + max(ys)) / 2, "h": max(ys) - min(ys), "t": texto.strip(), "c": float(conf)})
    cajas.sort(key=lambda c: c["yc"])
    filas = []
    for c in cajas:
        if filas and abs(c["yc"] - filas[-1][-1]["yc"]) < 0.6 * max(c["h"], filas[-1][-1]["h"]):
            filas[-1].append(c)
        else:
            filas.append([c])
    lineas = []
    for fila in filas:
        fila.sort(key=lambda c: c["x0"])
        trozos = [[fila[0]]]
        for c in fila[1:]:  # un hueco grande separa textos distintos en la misma altura
            if c["x0"] - trozos[-1][-1]["x1"] > 1.5 * max(c["h"], trozos[-1][-1]["h"]):
                trozos.append([c])
            else:
                trozos[-1].append(c)
        for tr in trozos:
            x0, x1 = min(c["x0"] for c in tr), max(c["x1"] for c in tr)
            if abs((x0 + x1) / 2 - ancho / 2) < 0.1 * ancho:
                lineas.append({"x0": x0, "x1": x1, "y0": min(c["y0"] for c in tr), "y1": max(c["y1"] for c in tr),
                               "texto": " ".join(c["t"] for c in tr), "cajas": tr})
    return lineas


def leer_texto(lector, arr):
    lineas = lineas_centradas(lector.readtext(arr), arr.shape[1])
    if not lineas:
        return "", 0.0, 0.0
    cajas = [c for l in lineas for c in l["cajas"]]
    peso = sum(len(c["t"]) for c in cajas)
    alto = round(float(np.median([c["h"] for c in cajas])) / arr.shape[1] * 1000, 1)  # alto de letra (‰ del ancho)
    return "\n".join(l["texto"] for l in lineas), round(sum(c["c"] * len(c["t"]) for c in cajas) / peso, 3), alto


def extraer_ocr(video, datos, zona, carpeta, fps):
    registros = carpeta / "ocr_fotogramas.jsonl"
    meta_ruta = carpeta / "ocr_meta.json"
    hecho = carpeta / "ocr_fotogramas.completo"
    capturas = carpeta / "capturas"
    meta = {"y0": zona["y0"], "y1": zona["y1"], "modo": zona["modo"], "fps": fps}
    if meta_ruta.exists() and leer_json(meta_ruta) != meta:
        print("  La zona o los fps han cambiado: rehago el OCR.")
        for p in (registros, hecho):
            p.unlink(missing_ok=True)
        shutil.rmtree(capturas, ignore_errors=True)
    if hecho.exists():
        print("  OCR ya hecho.")
        return
    guardar_json(meta_ruta, meta)
    capturas.mkdir(exist_ok=True)

    W, H = datos["ancho"], datos["alto"]
    cy = int(zona["y0"] * H) // 2 * 2
    ch = (int(zona["y1"] * H) - cy) // 2 * 2
    ow = min(1280, W) // 2 * 2
    oh = max(2, round(ch * ow / W / 2) * 2)

    inicio = 0.0
    if registros.exists() and registros.stat().st_size:
        ultima = registros.read_text(encoding="utf-8").strip().splitlines()[-1]
        inicio = json.loads(ultima)["t"]
        print(f"  Reanudando OCR desde {tc(inicio)}.")

    lector = lector_ocr()
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error"]
    if inicio:
        cmd += ["-ss", f"{inicio:.3f}"]
    cmd += ["-i", str(video), "-an", "-vf", f"fps={fps},crop={W}:{ch}:0:{cy},scale={ow}:{oh}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=ow * oh * 3 * 4)
    tam = ow * oh * 3
    total = int((datos["duracion"] - inicio) * fps)
    anterior, n_ocr, i, ultimo_ocr = None, 0, 0, -1e9
    with open(registros, "a", encoding="utf-8") as f, tqdm(total=total, unit="fot", desc="  Fotogramas") as barra:
        while True:
            buf = proc.stdout.read(tam)
            if len(buf) < tam:
                break
            t = round(inicio + i / fps, 3)
            i += 1
            barra.update(1)
            arr = np.frombuffer(buf, np.uint8).reshape(oh, ow, 3)
            firma = Firma(arr, zona["modo"])
            if not firma.distinta(anterior) and (firma.vacia or t - ultimo_ocr < 3):
                continue
            anterior, ultimo_ocr = firma, t
            if firma.vacia:
                texto, conf, alto = "", 0.0, 0.0
            else:
                texto, conf, alto = leer_texto(lector, arr)
                n_ocr += 1
            reg = {"t": t, "texto": texto, "conf": conf, "alto": alto}
            if texto:
                nombre = f"capturas/f_{int(round(t * 1000)):09d}.jpg"
                Image.fromarray(arr).save(carpeta / nombre, quality=82)
                reg["img"] = nombre
            f.write(json.dumps(reg, ensure_ascii=False) + "\n")
            f.flush()
            barra.set_postfix(ocr=n_ocr)
    if proc.wait() != 0:
        sys.exit("✖ ffmpeg falló extrayendo fotogramas. Vuelve a ejecutar para reanudar.")
    hecho.touch()


def parecidos(a, b):
    na, nb = " ".join(normalizar(a)), " ".join(normalizar(b))
    if not na or not nb:
        return na == nb
    if re.findall(r"\d+", na) != re.findall(r"\d+", nb):
        return False
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.75


def agrupar_subtitulos(carpeta, duracion, fps):
    regs = [json.loads(l) for l in (carpeta / "ocr_fotogramas.jsonl").read_text(encoding="utf-8").splitlines() if l]
    regs.sort(key=lambda r: r["t"])
    # tramo de cada registro: hasta el siguiente cambio
    for a, b in zip(regs, regs[1:] + [{"t": duracion}]):
        a["fin"] = b["t"]
    # quita huecos vacíos muy cortos (fallos puntuales de OCR o fundidos)
    regs = [r for r in regs if r["texto"] or r["fin"] - r["t"] >= 0.4]

    grupos = []
    for r in regs:
        if grupos and parecidos(grupos[-1][-1]["texto"], r["texto"]):
            grupos[-1].append(r)
        else:
            grupos.append([r])

    def contenido_en(corto, otro):
        a, b = " ".join(normalizar(corto)), " ".join(normalizar(otro or ""))
        return bool(a) and (a in b or difflib.SequenceMatcher(None, a, b).find_longest_match(
            0, len(a), 0, len(b)).size >= 0.7 * len(a))

    subs = []
    for k, g in enumerate(grupos):
        if not g[0]["texto"]:
            continue
        ini, fin = g[0]["t"], g[-1]["fin"]
        mejor = max(g, key=lambda r: (r["conf"], len(r["texto"])))
        if mejor["conf"] < 0.3:
            continue
        if fin - ini < 0.4:  # muy corto: solo es resto de un fundido si es parte del texto vecino
            vecinos = [grupos[j][0]["texto"] for j in (k - 1, k + 1) if 0 <= j < len(grupos)]
            if mejor["conf"] < 0.4 or any(contenido_en(mejor["texto"], v) for v in vecinos):
                continue
        subs.append({"id": len(subs) + 1, "inicio": round(ini, 2), "fin": round(fin, 2),
                     "texto_ocr": mejor["texto"], "confianza_ocr": mejor["conf"], "captura": mejor["img"],
                     "alto": mejor.get("alto")})
    # texto mucho más grande que el subtítulo habitual = frase animada / de diseño
    altos = [s["alto"] for s in subs if s.get("alto")]
    tipico = float(np.median(altos)) if altos else 0
    for s in subs:
        s["texto_grande"] = bool(tipico and s.get("alto") and s["alto"] > 1.5 * tipico)
        s.pop("alto", None)
    guardar_json(carpeta / "subtitulos.json", subs)
    print(f"  {len(subs)} subtítulos detectados (precisión de tiempos ±{1 / fps:.2f} s).")
    return subs


# --------------------------------------------------------------------------- 4. Revisión pendiente

def preparar_revision(carpeta, subs, url, nombre, duracion, prueba):
    trans = leer_json(carpeta / "transcripcion.json")
    palabras = sorted((p for s in trans["segmentos"] for p in s["palabras"]), key=lambda p: p[1])
    inicios = [p[1] for p in palabras]

    def tramo(a, b):
        j = bisect.bisect_left(inicios, a - 15)
        return [p for p in palabras[j:bisect.bisect_right(inicios, b)] if p[2] > a]

    def texto(ps):
        return " ".join(p[0] for p in ps).strip()

    items = []
    for s in subs:
        ini, fin = s["inicio"], s["fin"]
        dentro = tramo(ini - 0.3, fin + 0.3)
        ventana = tramo(ini - 2.5, fin + 2.5)
        item = dict(s)
        item["tiempo"] = f"{tc(ini, True)} → {tc(fin, True)}"
        item["audio_texto"] = texto(dentro)
        item["audio_antes"] = texto([p for p in ventana if p[2] <= ini - 0.3])
        item["audio_despues"] = texto([p for p in ventana if p[1] >= fin + 0.3])
        # pista de sincronía: dónde suenan en el audio la primera y la última palabra del subtítulo
        sw, aw = normalizar(s["texto_ocr"]), [" ".join(normalizar(p[0])) for p in ventana]
        bloques = [b for b in difflib.SequenceMatcher(None, sw, aw, autojunk=False).get_matching_blocks() if b.size]
        coincid = sum(b.size for b in bloques) / max(len(sw), 1)
        if bloques and coincid >= 0.4:
            primero, ultimo = bloques[0], bloques[-1]
            item["voz_inicio"] = ventana[primero.b][1]
            item["voz_fin"] = ventana[ultimo.b + ultimo.size - 1][2]
            item["desfase_entrada"] = round(ini - item["voz_inicio"], 2)   # >0: el subtítulo llega tarde
            item["desfase_salida"] = round(fin - item["voz_fin"], 2)       # <0: se va antes de acabar la frase
            item["pista_parcial"] = primero.a != 0 or ultimo.a + ultimo.size != len(sw)
        item["coincidencia_audio"] = round(coincid, 2)
        items.append(item)

    cabecera = {
        "video": nombre, "url": url, "duracion": round(duracion, 1), "prueba_minutos": prueba,
        "motor_transcripcion": trans["motor"], "total_subtitulos": len(items),
        "nota": "Tiempos en segundos. Capturas relativas a esta carpeta. Ver CLAUDE.md para los criterios.",
    }
    guardar_json(carpeta / "revision_pendiente.json", {**cabecera, "subtitulos": items})
    bloques_dir = carpeta / "bloques"
    shutil.rmtree(bloques_dir, ignore_errors=True)
    bloques_dir.mkdir()
    n = 0
    for n, k in enumerate(range(0, len(items), TAM_BLOQUE), 1):
        guardar_json(bloques_dir / f"bloque_{n:03d}.json", {**cabecera, "bloque": n, "subtitulos": items[k:k + TAM_BLOQUE]})
    return n


# --------------------------------------------------------------------------- 5. Informe

def captura_b64(carpeta, rel, ancho=900):
    img = Image.open(carpeta / rel).convert("RGB")
    if img.width > ancho:
        img = img.resize((ancho, round(img.height * ancho / img.width)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=72)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def cargar_fallos(carpeta, subs):
    datos = leer_json(carpeta / "fallos.json")
    fallos = datos.get("fallos", []) if isinstance(datos, dict) else datos
    por_id = {s["id"]: s for s in subs}
    filas = []
    for f in fallos:
        s = por_id.get(f.get("id"))
        if not s:
            aviso(f"fallos.json: el id {f.get('id')} no existe; lo ignoro.")
            continue
        tipo = f.get("tipo", "").lower().replace("í", "i").replace("ó", "o")
        filas.append({**f, "tipo": tipo, "inicio": s["inicio"], "fin": s["fin"], "captura": s["captura"],
                      "texto_actual": f.get("texto_actual") or s["texto_ocr"]})
    filas.sort(key=lambda f: (f["inicio"], f["tipo"]))
    return filas


CSS = """
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1f;--muted:#6b6b70;--line:#e3e1dc;--acc:#0b63ce;
--ortografia:#c2410c;--fidelidad:#7c3aed;--sincronia:#0f766e;--editor:#166534}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{padding:24px 28px 8px}h1{margin:0 0 4px;font-size:22px}.sub{color:var(--muted);font-size:13px}
.resumen{display:flex;flex-wrap:wrap;gap:10px;padding:12px 28px}
.chip{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 16px;min-width:130px}
.chip b{display:block;font-size:26px;line-height:1.1}.chip span{font-size:12px;color:var(--muted)}
.chip.t-ortografia b{color:var(--ortografia)}.chip.t-fidelidad b{color:var(--fidelidad)}.chip.t-sincronia b{color:var(--sincronia)}
.chip.t-editor b{color:var(--editor)}
.reproductor{position:sticky;top:0;z-index:5;background:#000;display:flex;justify-content:center}
video{width:100%;max-height:46vh;background:#000}
.barra{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:14px 28px 0}
.barra .sep{flex:1}
button{font:inherit;cursor:pointer}
.filtros button,.barra button{border:1px solid var(--line);background:var(--card);border-radius:20px;padding:5px 14px;font-size:13px;color:var(--ink)}
.filtros button.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.barra label{font-size:13px;color:var(--muted);display:flex;gap:6px;align-items:center}
.ayuda{padding:8px 28px 0;font-size:12.5px;color:var(--muted)}
.tabla{padding:12px 28px 40px}table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th{text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);background:#faf9f7;padding:10px;border-bottom:1px solid var(--line)}
td{padding:10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
a.min{color:var(--acc);font-weight:600;font-variant-numeric:tabular-nums;text-decoration:none;white-space:nowrap}
a.min:hover{text-decoration:underline}
img.cap{width:320px;max-width:100%;border-radius:6px;display:block;background:#000}
.actual{white-space:pre-line}.corr{white-space:pre-line;color:#166534;font-weight:600}
[contenteditable]{outline:none;border-radius:4px;padding:2px 4px;margin:-2px -4px}
[contenteditable]:hover{background:#f3f1ec}[contenteditable]:focus{background:#fff;box-shadow:0 0 0 2px var(--acc)}
.tipo{display:inline-block;padding:2px 9px;border-radius:12px;color:#fff;font-size:12px;white-space:nowrap}
.tipo.ortografia{background:var(--ortografia)}.tipo.fidelidad{background:var(--fidelidad)}.tipo.sincronia{background:var(--sincronia)}
.dudoso{display:inline-block;margin-top:6px;font-size:11.5px;color:#92400e;background:#fef3c7;border-radius:10px;padding:1px 8px}
.expl{color:var(--muted);font-size:13.5px}
td.sel{width:34px;padding-right:0}
td.sel input,th.sel input{width:17px;height:17px;cursor:pointer;accent-color:var(--ink)}
tbody tr{cursor:pointer}tbody tr:hover td{background:#fbfaf7}
tr.seleccionada td{background:#eef4ff!important}
tr.descartado td{opacity:.42}tr.descartado td.sel{opacity:1}
tr.descartado .corr,tr.descartado .actual{text-decoration:line-through}
.marca{display:inline-block;margin-top:6px;font-size:11.5px;border-radius:10px;padding:1px 8px}
.marca.ed{color:#fff;background:var(--editor)}
.acciones{position:sticky;bottom:0;z-index:6;display:none;gap:8px;align-items:center;flex-wrap:wrap;
background:var(--ink);color:#fff;padding:10px 28px;box-shadow:0 -4px 16px rgba(0,0,0,.15)}
.acciones.visible{display:flex}.acciones b{margin-right:6px}
.acciones button{border:1px solid #555;background:#2c2c2e;color:#fff;border-radius:18px;padding:6px 14px;font-size:13px}
.acciones button.prim{background:var(--editor);border-color:var(--editor);font-weight:600}
.acciones .sep{flex:1}
tr.activa td{background:#fff8db}
.oculto{display:none}
.vacio{padding:30px;text-align:center;color:var(--muted)}
@media screen and (max-width:760px){header,.resumen,.barra,.ayuda,.tabla,.acciones{padding-left:12px;padding-right:12px}
table,thead,tbody,tr,td{display:block}thead{display:none}tr{border-bottom:1px solid var(--line)}td{border:0;padding:6px 10px}}
@media print{.reproductor,.barra,.ayuda,.acciones,.dudoso,.marca,td.sel,th.sel{display:none!important}
body.imprimir-sel tbody tr:not(.seleccionada){display:none!important}tr.seleccionada td{background:none!important}}
"""

CSS_PDF = """
@page{size:A4 landscape;margin:12mm}
body{background:#fff;font-size:11px}header{padding:0 0 6px}.resumen{padding:6px 0}
.chip{padding:6px 12px;min-width:110px}.chip b{font-size:20px}
.tabla{padding:6px 0}th,td{padding:6px}td:nth-child(2){width:330px}img.cap{width:320px}
tr{page-break-inside:avoid}a.min{color:var(--acc)}
"""

JS_INFORME = """
const v=document.getElementById('v');
const CLAVE='revision-subtitulos:'+INFO.clave;
let estado={};try{estado=JSON.parse(localStorage.getItem(CLAVE))||{}}catch(e){}
const guardar=()=>{try{localStorage.setItem(CLAVE,JSON.stringify(estado))}catch(e){}};
const filas=[...document.querySelectorAll('tbody tr[data-k]')];
const est=k=>(estado[k]=estado[k]||{});
let filtroTipo='',ocultarDescartados=false,ultima=null;
function ir(t,a){if(!v)return;v.currentTime=Math.max(0,t-0.5);v.play();
  filas.forEach(r=>r.classList.remove('activa'));a.closest('tr').classList.add('activa');
  window.scrollTo({top:0,behavior:'smooth'});}
function pintar(tr){const st=estado[tr.dataset.k]||{};
  const desc=st.descartado!=null?st.descartado:tr.dataset.dudoso==='1';
  tr.classList.toggle('descartado',desc);
  tr.querySelector('.marca.ed').style.display=st.editor?'':'none';}
const visibles=()=>filas.filter(r=>!r.classList.contains('oculto'));
const seleccion=()=>filas.filter(r=>r.classList.contains('seleccionada'));
function seleccionar(tr,on){tr.classList.toggle('seleccionada',on);tr.querySelector('td.sel input').checked=on;}
function visibilidad(){filas.forEach(r=>{const o=(filtroTipo&&r.dataset.tipo!==filtroTipo)||
  (ocultarDescartados&&r.classList.contains('descartado'));r.classList.toggle('oculto',o);if(o)seleccionar(r,false);});barra();}
function barra(){const n=seleccion().length,vis=visibles();
  document.getElementById('acciones').classList.toggle('visible',n>0);
  document.getElementById('n-sel').textContent=n+(n===1?' seleccionado':' seleccionados');
  const todo=document.getElementById('sel-todos');todo.checked=n>0&&n===vis.length;todo.indeterminate=n>0&&n<vis.length;}
function contar(){const vivos=filas.filter(r=>!r.classList.contains('descartado'));
  document.querySelectorAll('[data-cuenta]').forEach(el=>{const t=el.dataset.cuenta;
    el.textContent=t==='total'?vivos.length:t==='editor'?filas.filter(r=>(estado[r.dataset.k]||{}).editor).length:
    vivos.filter(r=>r.dataset.tipo===t).length;});}
filas.forEach(tr=>{const k=tr.dataset.k,st=estado[k]||{};pintar(tr);
  ['corr','expl'].forEach(c=>{const el=tr.querySelector('.'+c);if(st[c]!=null)el.innerText=st[c];
    el.addEventListener('input',()=>{est(k)[c]=el.innerText;guardar();});});
  tr.addEventListener('click',e=>{if(e.target.closest('a,[contenteditable],video'))return;
    const i=filas.indexOf(tr),on=!tr.classList.contains('seleccionada');
    if(e.shiftKey&&ultima!=null){const [a,b]=[Math.min(ultima,i),Math.max(ultima,i)];
      filas.slice(a,b+1).filter(r=>!r.classList.contains('oculto')).forEach(r=>seleccionar(r,on));
      window.getSelection().removeAllRanges();}
    else seleccionar(tr,on);
    ultima=i;barra();});});
document.getElementById('sel-todos').onclick=e=>{e.stopPropagation();const on=e.target.checked;
  visibles().forEach(r=>seleccionar(r,on));barra();};
document.querySelectorAll('.filtros button').forEach(b=>b.onclick=()=>{filtroTipo=b.dataset.f;
  document.querySelectorAll('.filtros button').forEach(x=>x.classList.toggle('on',x===b));visibilidad();});
document.getElementById('ocultar').onchange=e=>{ocultarDescartados=e.target.checked;visibilidad();};
function aplicar(f){seleccion().forEach(r=>{f(est(r.dataset.k));pintar(r);});guardar();contar();}
document.getElementById('a-descartar').onclick=()=>{aplicar(s=>{s.descartado=true;s.editor=false;});limpiar();visibilidad();};
document.getElementById('a-restaurar').onclick=()=>{aplicar(s=>{s.descartado=false;});limpiar();};
function limpiar(){filas.forEach(r=>seleccionar(r,false));barra();}
document.getElementById('a-limpiar').onclick=limpiar;
document.getElementById('reiniciar').onclick=()=>{if(!confirm('¿Volver a la selección y los textos originales?'))return;
  estado={};guardar();location.reload();};
function bytesDe(img){const b=atob(img.src.split(',')[1]),u=new Uint8Array(b.length);
  for(let i=0;i<b.length;i++)u[i]=b.charCodeAt(i);return u;}
document.getElementById('a-word').onclick=()=>{const sel=seleccion();
  aplicar(s=>{s.editor=true;s.descartado=false;});
  const datos=crearDocx({titulo:INFO.titulo,subtitulo:sel.length+(sel.length===1?' cambio':' cambios')+' para el editor · '+
    new Date().toLocaleDateString('es-ES'),enlace:INFO.url,filas:sel.map(r=>{const img=r.querySelector('img.cap');return{
      minuto:r.querySelector('a.min').textContent,imagen:{bytes:bytesDe(img),ancho:img.naturalWidth,alto:img.naturalHeight},
      actual:r.querySelector('.actual').innerText,correccion:r.querySelector('.corr').innerText,
      tipo:r.querySelector('.tipo').textContent,explicacion:r.querySelector('.expl').innerText};})});
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([datos],
    {type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}));
  a.download=INFO.archivo+'_para_editor.docx';document.body.appendChild(a);a.click();
  setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove();},1000);};
document.getElementById('a-pdf').onclick=()=>{aplicar(s=>{s.editor=true;s.descartado=false;});
  document.body.classList.add('imprimir-sel');window.print();document.body.classList.remove('imprimir-sel');};
contar();visibilidad();
"""


def generar_informe(carpeta, url_raw, nombre, prueba):
    subs = leer_json(carpeta / "subtitulos.json")
    filas = cargar_fallos(carpeta, subs)
    titulo = f"Revisión de subtítulos · {nombre}"
    fecha = time.strftime("%d/%m/%Y")
    extra = f" · Prueba: primeros {prueba:g} min" if prueba else ""
    sub = f"{len(subs)} subtítulos revisados · {len(filas)} fallos detectados · {fecha}{extra}"
    for f in filas:
        f["b64"] = captura_b64(carpeta, f["captura"])

    def resumen(lista, interactivo):
        def n(clave, valor):
            return f'<b data-cuenta="{clave}">{valor}</b>' if interactivo else f"<b>{valor}</b>"
        chips = [f'<div class="chip">{n("total", len(lista))}<span>Fallos</span></div>']
        chips += [f'<div class="chip t-{k}">{n(k, sum(1 for f in lista if f["tipo"] == k))}<span>{v}</span></div>'
                  for k, v in TIPOS.items()]
        if interactivo:
            chips.append(f'<div class="chip t-editor">{n("editor", 0)}<span>Para el editor</span></div>')
        return '<section class="resumen">' + "".join(chips) + "</section>"

    def tabla(lista, interactivo):
        if not lista:
            return '<div class="vacio">No se han encontrado fallos. 🎉</div>'
        cuerpo = []
        for f in lista:
            t = f["inicio"]
            ed = ' contenteditable="true" spellcheck="true"' if interactivo else ""
            if interactivo:
                minuto = f'<a class="min" href="#" onclick="ir({t:.2f},this);return false">{tc(t)}</a>'
                controles = '<br><span class="marca ed">En el Word</span>'
                if f.get("dudoso"):
                    controles += '<br><span class="dudoso">¿Intencionado?</span>'
                atrs = (f' data-k="{f["id"]}-{f["tipo"]}" data-tipo="{f["tipo"]}"'
                        f' data-dudoso="{1 if f.get("dudoso") else 0}"')
                casilla = '<td class="sel"><input type="checkbox" tabindex="-1"></td>'
            else:
                minuto, controles, atrs, casilla = f'<a class="min" href="{html.escape(url_raw)}">{tc(t)}</a>', "", "", ""
            cuerpo.append(
                f'<tr{atrs}>{casilla}<td>{minuto}{controles}</td>'
                f'<td><img class="cap" src="{f["b64"]}" alt="captura"></td>'
                f'<td class="actual">{html.escape(f["texto_actual"])}</td>'
                f'<td class="corr"{ed}>{html.escape(f.get("correccion", ""))}</td>'
                f'<td><span class="tipo {f["tipo"]}">{TIPOS.get(f["tipo"], f["tipo"])}</span></td>'
                f'<td class="expl"{ed}>{html.escape(f.get("explicacion", ""))}</td></tr>')
        sel = '<th class="sel"><input type="checkbox" id="sel-todos" title="Seleccionar todos"></th>' if interactivo else ""
        return (f'<table><thead><tr>{sel}<th>Minuto</th><th>Captura</th><th>Texto actual</th>'
                '<th>Corrección propuesta</th><th>Tipo</th><th>Explicación</th></tr></thead><tbody>'
                + "".join(cuerpo) + "</tbody></table>")

    cab = f'<header><h1>{html.escape(titulo)}</h1><div class="sub">{html.escape(sub)}</div></header>'
    barra = ('<div class="barra"><div class="filtros" style="display:flex;gap:8px;flex-wrap:wrap">'
             '<button class="on" data-f="">Todos</button>'
             + "".join(f'<button data-f="{k}">{v}</button>' for k, v in TIPOS.items())
             + '</div><label><input type="checkbox" id="ocultar"> Ocultar descartados</label><span class="sep"></span>'
             '<button id="reiniciar">Reiniciar</button></div>'
             '<div class="ayuda">Selecciona los fallos que quieras (clic en la fila o en la casilla; '
             '<b>Mayús+clic</b> para un rango) y elige qué hacer con ellos en la barra de abajo. '
             'Puedes corregir la propuesta y la explicación haciendo clic en el texto. Todo se guarda en este navegador.</div>')
    acciones = ('<div class="acciones" id="acciones"><b id="n-sel"></b>'
                '<button id="a-word" class="prim">Descargar Word para el editor</button>'
                '<button id="a-pdf">PDF para el editor</button>'
                '<button id="a-descartar">Descartar</button>'
                '<button id="a-restaurar">Restaurar</button>'
                '<span class="sep"></span><button id="a-limpiar">Quitar selección</button></div>')
    info = {"clave": f"{nombre}|{prueba or ''}", "titulo": titulo, "url": url_raw,
            "archivo": re.sub(r"[^\w\-]+", "_", Path(nombre).stem)}
    docx_js = (RAIZ / "docx_mini.js").read_text(encoding="utf-8")
    pagina = (f'<!doctype html><html lang="es"><head><meta charset="utf-8">'
              f'<meta name="viewport" content="width=device-width,initial-scale=1">'
              f'<title>{html.escape(titulo)}</title><style>{CSS}</style></head><body>'
              f'{cab}{resumen(filas, True)}<div class="reproductor"><video id="v" controls preload="metadata" '
              f'src="{html.escape(url_raw)}"></video></div>{barra}<section class="tabla">{tabla(filas, True)}</section>{acciones}'
              f'<script>{docx_js}</script>'
              f'<script>const INFO={json.dumps(info, ensure_ascii=False)};{JS_INFORME}</script></body></html>')
    ruta_html = carpeta / "informe_revision.html"
    ruta_html.write_text(pagina, encoding="utf-8")

    # PDF automático: todos los fallos salvo los marcados como posiblemente intencionados
    seguros = [f for f in filas if not f.get("dudoso")]
    enlace = f'<div class="sub">Vídeo: <a href="{html.escape(url_raw)}">{html.escape(url_raw)}</a></div>'
    pdf_html = (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{html.escape(titulo)}</title>'
                f'<style>{CSS}{CSS_PDF}</style></head><body>{cab}{enlace}{resumen(seguros, False)}'
                f'<section class="tabla">{tabla(seguros, False)}</section></body></html>')
    tmp = carpeta / "_informe_pdf.html"
    tmp.write_text(pdf_html, encoding="utf-8")
    ruta_pdf = carpeta / "informe_revision.pdf"
    ok = html_a_pdf(tmp, ruta_pdf)
    tmp.unlink(missing_ok=True)
    print(f"  HTML: {ruta_html}")
    print(f"  PDF:  {ruta_pdf}" if ok else "  ⚠️  No encontré Chrome/Chromium/Edge para generar el PDF.")
    cuenta = {k: sum(1 for f in filas if f["tipo"] == k) for k in TIPOS}
    dudosos = len(filas) - len(seguros)
    print("  Resumen: " + ", ".join(f"{v}: {cuenta[k]}" for k, v in TIPOS.items()) + f" · Total: {len(filas)}"
          + (f" ({dudosos} posiblemente intencionados, descartados por defecto)" if dudosos else ""))


def html_a_pdf(origen, destino):
    candidatos = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser"),
    ]
    for exe in filter(None, candidatos):
        if not Path(exe).exists():
            continue
        r = subprocess.run([exe, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                            f"--print-to-pdf={destino}", origen.resolve().as_uri()],
                           capture_output=True, timeout=300)
        if r.returncode == 0 and destino.exists():
            return True
    return False


# --------------------------------------------------------------------------- principal

def main():
    ap = argparse.ArgumentParser(description="Revisa subtítulos quemados de un vídeo de Dropbox.")
    ap.add_argument("enlace", help="Enlace de Dropbox al vídeo")
    ap.add_argument("--zona", help="Franja de subtítulos 'inicio,fin' en fracción de la altura (0.80,0.96) o en píxeles")
    ap.add_argument("--fps", type=float, default=3, help="Fotogramas por segundo a analizar (defecto 3)")
    ap.add_argument("--motor", default="auto", choices=["auto", "mlx", "faster-whisper", "openai"],
                    help="Motor de transcripción (auto: GPU NVIDIA -> faster-whisper; Mac M -> mlx)")
    ap.add_argument("--prueba", type=float, metavar="MIN", help="Procesar solo los primeros MIN minutos")
    ap.add_argument("--informe", action="store_true", help="Solo generar el informe a partir de fallos.json")
    ap.add_argument("--rehacer", nargs="+", default=[], choices=["transcripcion", "zona", "ocr"],
                    help="Borra y repite estos pasos")
    args = ap.parse_args()

    url_dl, url_raw = enlaces_dropbox(args.enlace)
    nombre = nombre_desde_url(args.enlace)
    base = Path(nombre).stem + (f"__prueba_{args.prueba:g}min" if args.prueba else "")
    carpeta = TRABAJOS / base
    carpeta.mkdir(parents=True, exist_ok=True)
    print(f"Carpeta de trabajo: {carpeta}")

    for paso in args.rehacer:
        if paso == "transcripcion":
            (carpeta / "transcripcion.json").unlink(missing_ok=True)
        if paso == "zona":
            (carpeta / "zona.json").unlink(missing_ok=True)
        if paso in ("zona", "ocr"):
            (carpeta / "ocr_fotogramas.completo").unlink(missing_ok=True)
            (carpeta / "ocr_fotogramas.jsonl").unlink(missing_ok=True)

    if args.informe:
        if not (carpeta / "fallos.json").exists():
            sys.exit(f"✖ No existe {carpeta / 'fallos.json'}. Primero hay que hacer la revisión.")
        info("Generando informe…")
        generar_informe(carpeta, url_raw, nombre, args.prueba)
        return

    video = carpeta / ("video" + (Path(nombre).suffix or ".mp4"))
    info("1/4 Descarga")
    if args.prueba:
        descargar_prueba(url_dl, video, args.prueba)
    else:
        descargar(url_dl, video)
    datos = datos_video(video)
    print(f"  {datos['ancho']}x{datos['alto']}, {datos['fps']:g} fps, duración {tc(datos['duracion'])}")

    info("2/4 Audio y transcripción")
    wav = carpeta / "audio.wav"
    extraer_audio(video, wav)
    transcribir(wav, carpeta / "transcripcion.json", args.motor, datos["duracion"])

    info("3/4 Subtítulos (zona, cambios y OCR)")
    zona = detectar_zona(video, datos, carpeta, args.zona)
    extraer_ocr(video, datos, zona, carpeta, args.fps)
    subs = agrupar_subtitulos(carpeta, datos["duracion"], args.fps)

    info("4/4 Preparando la revisión")
    n_bloques = preparar_revision(carpeta, subs, args.enlace, nombre, datos["duracion"], args.prueba)
    print(f"  {carpeta / 'revision_pendiente.json'}  ({n_bloques} bloques de {TAM_BLOQUE} en bloques/)")

    if (carpeta / "fallos.json").exists():
        info("Existe fallos.json: generando informe…")
        generar_informe(carpeta, url_raw, nombre, args.prueba)
    else:
        print(f"\n✅ Listo para revisar. Pide a Claude Code: «revisa los subtítulos de {carpeta.relative_to(RAIZ)}».\n"
              f"   Cuando exista fallos.json, ejecuta de nuevo con --informe.")


if __name__ == "__main__":
    main()
