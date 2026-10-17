"""
AGENTE TIKTOK v2 — vídeos de política, rápido y sin cuelgues  (@politicasinfiltrosesp)
-------------------------------------------------------------------------------------
Qué mejora respecto a la v1:
  • SIN Whisper (era lo que se colgaba): la voz ya da el tiempo exacto de cada palabra.
  • Voz Microsoft Edge (gratis, muy natural). Si no hay internet, usa Piper en tu PC.
  • TODO el vídeo se monta en UNA sola pasada de FFmpeg con tu RTX (NVENC).
  • Descargas en paralelo y tiempo máximo en cada paso: si algo falla, salta y sigue.
  • Instala solo lo que le falte la primera vez.

Usar:
  python agente_tiktok_v2.py                 -> hace CANTIDAD vídeos con las noticias de hoy
  python agente_tiktok_v2.py --num 1         -> solo 1
  python agente_tiktok_v2.py "tema o texto"  -> vídeo de tu tema
  python agente_tiktok_v2.py https://...     -> vídeo de esa noticia
  python agente_tiktok_v2.py --piper         -> fuerza voz 100% local
Necesita: Python 3.10+ y Ollama abierto (ollama pull qwen2.5:7b)
"""
# ============================ AJUSTES ============================
CANAL = "@politicasinfiltrosesp"
CANTIDAD = 3                       # vídeos por ejecución
MODELO_IA = "qwen2.5:7b"           # 16 GB VRAM: prueba "qwen2.5:14b"
PALABRAS = 140                     # ~50 s de vídeo
VOZ_EDGE = "es-ES-AlvaroNeural"    # mujer: "es-ES-ElviraNeural"
VELOCIDAD_EDGE = "+12%"
VOZ_PIPER = "es_ES-davefx-medium"  # voz local de reserva
COLOR = "&H00FFFF&"                # palabra resaltada: amarillo (&H00FF66& verde)
SEGUNDOS_POR_FOTO = 4
VOLUMEN_MUSICA = 0.08              # mete mp3 sin copyright en la carpeta "musica"
HASHTAGS_FIJOS = ["#politica", "#españa", "#noticias", "#politicasinfiltros"]
FEEDS = {
    "El País": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/espana/portada",
    "El Mundo": "https://e00-elmundo.uecdn.es/elmundo/rss/espana.xml",
    "ABC": "https://www.abc.es/rss/2.0/espana/",
    "elDiario.es": "https://www.eldiario.es/rss/politica/",
    "Europa Press": "https://www.europapress.es/rss/rss.aspx?ch=00066",
    "20minutos": "https://www.20minutos.es/rss/nacional/",
}
# =================================================================

import asyncio, html, importlib, io, json, math, os, random, re, shutil, subprocess, sys, time, unicodedata, wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
SALIDA, CACHE = AQUI / "salida", AQUI / "_cache"
HIST = AQUI / "historial.json"
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def asegurar(paquetes):
    """Instala lo que falte (solo la primera vez)."""
    faltan = []
    for mod, pip in paquetes:
        try:
            importlib.import_module(mod)
        except ImportError:
            faltan.append(pip)
    if faltan:
        print(f"📦 Instalando: {' '.join(faltan)} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *faltan])


asegurar([("requests", "requests"), ("feedparser", "feedparser"), ("trafilatura", "trafilatura"),
          ("edge_tts", "edge-tts"), ("PIL", "pillow"), ("static_ffmpeg", "static-ffmpeg")])
import requests  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 AgenteVideosPolitica/2.0"}


def log(msg):
    print(msg, flush=True)


def ff(cmd, cwd=None, limite=600):
    """Ejecuta FFmpeg con tiempo máximo (nunca se queda colgado)."""
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=limite)
    if p.returncode:
        raise RuntimeError("FFmpeg: " + p.stderr[-1200:])
    return p.stdout


def bajar(url, destino, limite=60):
    if not destino.exists():
        r = requests.get(url, headers=UA, timeout=limite)
        r.raise_for_status()
        destino.write_bytes(r.content)
    return destino


def slug(t):
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()[:45] or "video"


# =================== 1. NOTICIAS ===================
def historial():
    try:
        return set(json.loads(HIST.read_text(encoding="utf-8")))
    except Exception:
        return set()


def apuntar(link):
    h = historial() | {link}
    HIST.write_text(json.dumps(sorted(h)[-2000:], ensure_ascii=False), encoding="utf-8")


def noticias_de_hoy():
    import feedparser
    usadas = historial()

    def leer(item):
        fuente, url = item
        try:
            d = feedparser.parse(requests.get(url, headers=UA, timeout=15).content)
            return [{"fuente": fuente, "titulo": html.unescape(e.title).strip(), "link": e.link}
                    for e in d.entries[:8] if e.get("link") and e.link not in usadas]
        except Exception:
            return []
    with ThreadPoolExecutor(8) as ex:
        res = [n for lista in ex.map(leer, FEEDS.items()) for n in lista]
    vistos, unicas = set(), []
    for n in res:
        if n["titulo"].lower() not in vistos:
            vistos.add(n["titulo"].lower()); unicas.append(n)
    return unicas


def leer_articulo(url):
    import trafilatura
    try:
        h = requests.get(url, headers=UA, timeout=15).text
        meta = trafilatura.extract_metadata(h)
        return (meta.title if meta and meta.title else ""), (trafilatura.extract(h) or "")[:4500]
    except Exception:
        return "", ""


# =================== 2. IA (Ollama) ===================
def ia(sistema, usuario, temp=0.6):
    for intento in range(2):
        try:
            r = requests.post("http://localhost:11434/api/chat", timeout=300, json={
                "model": MODELO_IA, "format": "json", "stream": False, "keep_alive": "15m",
                "options": {"temperature": temp, "num_ctx": 6144},
                "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}]})
        except requests.ConnectionError:
            sys.exit("❌ Ollama no está abierto. Ábrelo y ejecuta: ollama pull " + MODELO_IA)
        if r.status_code == 404:
            sys.exit(f"❌ Falta el modelo. Ejecuta: ollama pull {MODELO_IA}")
        r.raise_for_status()
        t = re.sub(r"<think>.*?</think>", "", r.json()["message"]["content"], flags=re.S)
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    raise RuntimeError("La IA no devolvió JSON válido")


def elegir(lista, n):
    if len(lista) <= n:
        return lista
    txt = "\n".join(f"{i}. [{x['fuente']}] {x['titulo']}" for i, x in enumerate(lista))
    d = ia("Eres editor de un TikTok de política española. Eliges noticias de impacto para la gente, "
           "de temas distintos, sin repetir la misma historia de distintos medios. Responde SOLO JSON.",
           f"{txt}\n\nElige las {n} mejores. JSON: {{\"elegidas\": [numeros]}}", 0.2)
    idx = []
    for v in d.get("elegidas", []):
        try:
            v = int(v)
            if 0 <= v < len(lista) and v not in idx:
                idx.append(v)
        except (TypeError, ValueError):
            pass
    idx += [i for i in range(len(lista)) if i not in idx]
    return [lista[i] for i in idx[:n]]


SIS_GUION = ("Eres guionista de TikTok de noticias políticas de España. Español de España, frases cortas y claras "
             "para leer en voz alta. Usa SOLO hechos del texto: no inventes cifras, fechas ni citas. Atribuye "
             "('según el Gobierno', 'denuncia el PP'). Tono neutral, sin insultos. Primera frase = gancho potente. "
             "Última frase = pregunta para comentar. Sin emojis, hashtags ni acotaciones. Responde SOLO JSON.")


def guion(titulo, texto, fuente):
    d = ia(SIS_GUION, f"""Noticia de {fuente}: {titulo}
{texto}

JSON:
{{"titulo": "titular para pantalla, máximo 7 palabras",
"guion": "unas {PALABRAS} palabras",
"descripcion": "1-2 frases para la publicación + pregunta",
"hashtags": ["6 hashtags con #"],
"fotos": ["4 búsquedas en español para Wikimedia Commons: nombre completo de políticos, instituciones o lugares de la noticia"]}}""")
    g = re.sub(r"\s+", " ", re.sub(r"[#*_\[\]{}<>|~]", "", str(d.get("guion", "")))).strip()
    if len(g.split()) < 30:
        raise RuntimeError("guion demasiado corto")
    d["guion"] = g
    d["titulo"] = str(d.get("titulo") or titulo)[:70]
    d["hashtags"] = [("#" + h.lstrip("#")).replace(" ", "") for h in d.get("hashtags", []) if h][:7]
    d["fotos"] = [str(f) for f in d.get("fotos", []) if f][:4] or ["Congreso de los Diputados", "Palacio de la Moncloa"]
    return d


# =================== 3. VOZ + TIEMPO DE CADA PALABRA ===================
def voz_edge(texto, mp3):
    import edge_tts

    async def run():
        palabras, audio = [], bytearray()
        c = edge_tts.Communicate(texto, VOZ_EDGE, rate=VELOCIDAD_EDGE, boundary="WordBoundary")
        async for ch in c.stream():
            if ch["type"] == "audio":
                audio += ch["data"]
            elif ch["type"] == "WordBoundary":
                ini = ch["offset"] / 1e7
                palabras.append([ch["text"], ini, ini + ch["duration"] / 1e7])
        return palabras, bytes(audio)
    palabras, audio = asyncio.run(asyncio.wait_for(run(), 90))
    if not audio or not palabras:
        raise RuntimeError("Edge no devolvió audio")
    mp3.write_bytes(audio)
    return palabras


def voz_piper(texto, wav_path):
    """Local. Genera frase a frase y reparte el tiempo entre palabras según su longitud."""
    asegurar([("piper", "piper-tts")])
    from piper import PiperVoice, SynthesisConfig
    CACHE.mkdir(exist_ok=True)
    idioma, locutor, calidad = VOZ_PIPER.split("-")
    base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/es/{idioma}/{locutor}/{calidad}/{VOZ_PIPER}"
    bajar(base + ".onnx.json", CACHE / f"{VOZ_PIPER}.onnx.json")
    modelo = bajar(base + ".onnx", CACHE / f"{VOZ_PIPER}.onnx", 300)
    v = PiperVoice.load(str(modelo))
    cfg = SynthesisConfig(length_scale=0.85)
    frases = [f for f in re.split(r"(?<=[.!?…])\s+", texto) if f.strip()]
    pcm, palabras, t, sr = bytearray(), [], 0.0, 22050
    for f in frases:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            v.synthesize_wav(f, w, syn_config=cfg)
        buf.seek(0)
        with wave.open(buf, "rb") as w:
            sr, datos = w.getframerate(), w.readframes(w.getnframes())
        dur = len(datos) / 2 / sr
        ws = f.split()
        pesos = [len(x) + (3 if x[-1] in ",;:" else 0) + 1 for x in ws]
        util, cursor = max(0.1, dur - 0.25), t + 0.08
        for x, p in zip(ws, pesos):
            d = util * p / sum(pesos)
            palabras.append([x.strip("¿¡.,;:!?\"'()"), cursor, cursor + d * 0.9])
            cursor += d
        pcm += datos
        t += dur
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(bytes(pcm))
    return [p for p in palabras if p[0]]


# =================== 4. SUBTÍTULOS ===================
def t_ass(t):
    t = max(0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def subtitulos(pal, dur, titulo, fuente, destino):
    esc = lambda s: s.replace("{", "(").replace("}", ")").replace("\\", "")
    L = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "ScaledBorderAndShadow: yes", "",
         "[V4+ Styles]",
         "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
         "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
         "MarginR, MarginV, Encoding",
         "Style: Sub,Anton,135,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,2,0,1,8,4,5,60,60,0,1",
         "Style: Tit,Anton,84,&H00FFFFFF,&H00FFFFFF,&H002B2BD9,&H00000000,0,0,0,0,100,100,1,0,3,18,0,8,90,90,300,1",
         "Style: Inf,Anton,44,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,3,2,8,60,60,200,1",
         "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
         f"Dialogue: 1,0:00:00.00,{t_ass(dur)},Tit,,0,0,0,,{esc(titulo.upper())}",
         f"Dialogue: 1,0:00:00.00,{t_ass(dur)},Inf,,0,0,0,,{esc(CANAL)} · Fuente: {esc(fuente)}"]
    grupos = [pal[i:i + 3] for i in range(0, len(pal), 3)]
    for gi, g in enumerate(grupos):
        sig = grupos[gi + 1][0][1] if gi + 1 < len(grupos) else dur
        for wi, (w, ini, fin) in enumerate(g):
            fin = g[wi + 1][1] if wi + 1 < len(g) else (sig if sig - fin < 0.7 else fin + 0.3)
            txt = " ".join(("{\\c" + COLOR + "\\fscx112\\fscy112}" + esc(x.upper()) + "{\\c&HFFFFFF&\\fscx100\\fscy100}")
                           if j == wi else esc(x.upper()) for j, (x, _, _) in enumerate(g))
            pop = "{\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)}" if wi == 0 else ""
            L.append(f"Dialogue: 0,{t_ass(ini)},{t_ass(max(fin, ini + 0.05))},Sub,,0,0,0,,"
                     f"{{\\an5\\pos(540,1400)}}{pop}{txt}")
    destino.write_text("\n".join(L) + "\n", encoding="utf-8")


# =================== 5. FOTOS (Wikimedia, sin clave) ===================
def buscar_fotos(busquedas, carpeta, maximo=6):
    def buscar(q):
        try:
            pags = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=15, params={
                "action": "query", "format": "json", "generator": "search", "gsrsearch": f"filetype:bitmap {q}",
                "gsrnamespace": 6, "gsrlimit": 10, "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
                "iiurlwidth": 1280, "iiextmetadatafilter": "Artist|LicenseShortName"}).json()["query"]["pages"]
            return sorted(pags.values(), key=lambda x: x.get("index", 99))
        except Exception:
            return []
    with ThreadPoolExecutor(4) as ex:
        resultados = list(ex.map(buscar, busquedas))
    elegidas, vistas = [], set()
    for paginas in resultados:  # máx. 2 por búsqueda para variar
        n = 0
        for p in paginas:
            ii, tit = (p.get("imageinfo") or [{}])[0], p.get("title", "")
            if (tit in vistas or ii.get("mime") != "image/jpeg" or ii.get("width", 0) < 800
                    or any(x in tit.lower() for x in ("logo", "map", "escudo", "firma", "chart", "diagram"))):
                continue
            vistas.add(tit); elegidas.append((tit, ii)); n += 1
            if n >= 2:
                break
    elegidas = elegidas[:maximo]

    def descargar(i_e):
        i, (tit, ii) = i_e
        try:
            f = bajar(ii.get("thumburl") or ii["url"], carpeta / f"raw{i}.jpg", 30)
            m = ii.get("extmetadata", {})
            autor = re.sub(r"<[^>]+>", "", html.unescape(m.get("Artist", {}).get("value", "?"))).strip()
            return f, f"{tit[5:]} — {autor} ({m.get('LicenseShortName', {}).get('value', '')}), Wikimedia Commons"
        except Exception:
            return None
    with ThreadPoolExecutor(6) as ex:
        ok = [r for r in ex.map(descargar, enumerate(elegidas)) if r]
    return [r[0] for r in ok], [r[1] for r in ok]


def componer(foto, destino):
    """Foto entera centrada sobre la misma foto desenfocada (1188x2112, un 10% extra para el movimiento)."""
    from PIL import Image, ImageFilter, ImageEnhance
    W, H = 1188, 2112
    im = Image.open(foto).convert("RGB")
    r = max(W / im.width, H / im.height)
    fondo = im.resize((int(im.width * r) + 1, int(im.height * r) + 1), Image.BILINEAR)
    fondo = fondo.crop(((fondo.width - W) // 2, (fondo.height - H) // 2, (fondo.width - W) // 2 + W,
                        (fondo.height - H) // 2 + H))
    fondo = ImageEnhance.Brightness(fondo.resize((W // 8, H // 8)).filter(ImageFilter.GaussianBlur(4))
                                    .resize((W, H), Image.BILINEAR)).enhance(0.6)
    r = min(W / im.width, 1600 / im.height)
    fg = im.resize((int(im.width * r), int(im.height * r)), Image.LANCZOS)
    fondo.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    fondo.save(destino, quality=92)
    return destino


# =================== 6. MONTAJE (una sola pasada) ===================
_COD = None


def codificador():
    global _COD
    if _COD is None:
        ok = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=1080x1920:d=0.3",
                             "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True, timeout=30).returncode == 0
        _COD = (["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "22", "-b:v", "0"] if ok
                else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"])
        log("   ⚡ Codificando con la GPU (NVENC)" if ok else "   ℹ Codificando con CPU")
    return _COD


def montar(imagenes, audio, ass, dur, tmp, salida):
    n = max(1, math.ceil(dur / SEGUNDOS_POR_FOTO))
    seg = dur / n + 0.05
    cmd, filtros = ["ffmpeg", "-y", "-v", "error"], []
    if imagenes:
        for i in range(n):
            cmd += ["-loop", "1", "-framerate", "30", "-t", f"{seg:.3f}", "-i", imagenes[i % len(imagenes)].name]
            movs = [("(iw-ow)*t/{s}", "(ih-oh)/2"), ("(iw-ow)*(1-t/{s})", "(ih-oh)/2"),
                    ("(iw-ow)/2", "(ih-oh)*t/{s}"), ("(iw-ow)/2", "(ih-oh)*(1-t/{s})")]
            x, y = (m.format(s=f"{seg:.3f}") for m in movs[i % 4])
            filtros.append(f"[{i}:v]crop=1080:1920:x='{x}':y='{y}',setsar=1,fps=30[v{i}]")
        filtros.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[bg]")
    else:
        cmd += ["-f", "lavfi", "-i", f"gradients=s=1080x1920:c0=0x0b1220:c1=0x3a0d12:speed=0.01:d={dur + 1:.2f}:r=30"]
        filtros.append("[0:v]null[bg]"); n = 1
    filtros.append(f"[bg]ass={ass.name}:fontsdir=.,scale=out_range=tv,format=yuv420p[v]")
    cmd += ["-i", audio.name]
    a = n
    musica = [p for p in (AQUI / "musica").glob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg")] \
        if (AQUI / "musica").exists() else []
    if musica:
        cmd += ["-stream_loop", "-1", "-i", str(random.choice(musica))]
        filtros.append(f"[{a}:a]aresample=48000[vz];[{a + 1}:a]aresample=48000,volume={VOLUMEN_MUSICA}[mu];"
                       f"[vz][mu]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        filtros.append(f"[{a}:a]aresample=48000[a]")
    cmd += ["-filter_complex", ";".join(filtros), "-map", "[v]", "-map", "[a]", *codificador(),
            "-r", "30", "-c:a", "aac", "-b:a", "128k", "-t", f"{dur + 0.3:.2f}", "-movflags", "+faststart",
            "video.mp4"]
    ff(cmd, cwd=tmp, limite=900)
    shutil.move(str(tmp / "video.mp4"), str(salida))


# =================== UN VÍDEO ===================
def hacer_video(noticia, forzar_piper=False):
    t0 = time.time()
    log(f"\n🎬 {noticia['titulo']}  ({noticia['fuente']})")
    carpeta = SALIDA / f"{datetime.now():%Y-%m-%d_%H%M%S}_{slug(noticia['titulo'])}"
    tmp = carpeta / "_tmp"
    tmp.mkdir(parents=True)
    try:
        texto = noticia.get("texto") or leer_articulo(noticia["link"])[1] or noticia["titulo"]
        log("   🧠 Guion con IA...")
        d = guion(noticia["titulo"], texto, noticia["fuente"])

        # La voz y las fotos se preparan A LA VEZ
        log("   🗣️  Voz + 🖼️  fotos (en paralelo)...")
        with ThreadPoolExecutor(2) as ex:
            f_fotos = ex.submit(buscar_fotos, d["fotos"], tmp)
            palabras, audio = None, None
            if not forzar_piper:
                try:
                    audio = tmp / "voz.mp3"
                    palabras = voz_edge(d["guion"], audio)
                except Exception as e:
                    log(f"   ⚠ Voz Edge no disponible ({str(e)[:60]}), uso Piper local")
            if not palabras:
                audio = tmp / "voz.wav"
                palabras = voz_piper(d["guion"], audio)
            fotos, creditos = f_fotos.result(timeout=120)
        dur = float(json.loads(ff(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                                   str(audio)], limite=30))["format"]["duration"])

        imagenes = [componer(f, tmp / f"img{i}.jpg") for i, f in enumerate(fotos)]
        fuente_ttf = bajar("https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf",
                           (CACHE.mkdir(exist_ok=True) or CACHE) / "Anton-Regular.ttf")
        shutil.copy(fuente_ttf, tmp)
        subtitulos(palabras, dur, d["titulo"], noticia["fuente"], tmp / "subs.ass")

        log(f"   🎞️  Montando {dur:.0f}s de vídeo...")
        montar(imagenes, audio, tmp / "subs.ass", dur, tmp, carpeta / "video.mp4")

        tags = " ".join(dict.fromkeys(d["hashtags"] + HASHTAGS_FIJOS))
        (carpeta / "publicacion.txt").write_text(
            f"{d.get('descripcion', '')}\n\n{tags}\n\n"
            f"Fuente: {noticia['fuente']} {noticia.get('link', '')}\n"
            + ("\nFotos (Wikimedia Commons):\n" + "\n".join(creditos) + "\n" if creditos else "")
            + f"\n---------- GUION (revísalo antes de subir) ----------\n{d['guion']}\n", encoding="utf-8")
        (carpeta / "info.json").write_text(json.dumps({
            "titulo": d["titulo"], "descripcion": d.get("descripcion", ""), "hashtags": tags.split(),
            "fuente": noticia["fuente"], "link": noticia.get("link", ""), "duracion": round(dur, 1),
            "guion": d["guion"], "creado": datetime.now().isoformat(timespec="seconds")},
            ensure_ascii=False, indent=2), encoding="utf-8")
        if noticia.get("link"):
            apuntar(noticia["link"])
        log(f"   ✅ Hecho en {time.time() - t0:.0f}s → {carpeta / 'video.mp4'}")
        return carpeta
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# =================== PRINCIPAL ===================
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("entrada", nargs="*", help="tema o URL (opcional)")
    ap.add_argument("--num", type=int, default=CANTIDAD)
    ap.add_argument("--piper", action="store_true", help="voz 100%% local")
    args = ap.parse_args()

    import static_ffmpeg
    static_ffmpeg.add_paths(weak=True)
    SALIDA.mkdir(exist_ok=True)
    entrada = " ".join(args.entrada).strip()
    t0 = time.time()

    if entrada.lower().startswith("http"):
        titulo, texto = leer_articulo(entrada)
        if not texto:
            sys.exit("No pude leer esa URL")
        lista = [{"titulo": titulo or entrada, "texto": texto, "link": entrada,
                  "fuente": re.sub(r"^www\.", "", entrada.split("/")[2])}]
    elif entrada:
        lista = [{"titulo": entrada[:110], "texto": entrada, "link": "", "fuente": "Redacción"}]
    else:
        log("📰 Leyendo periódicos...")
        todas = noticias_de_hoy()
        if not todas:
            sys.exit("No hay noticias nuevas.")
        log(f"🧠 Eligiendo las {args.num} mejores de {len(todas)}...")
        lista = elegir(todas, args.num)

    hechos = []
    for n in lista:
        try:
            hechos.append(hacer_video(n, args.piper))
        except Exception as e:
            log(f"   ❌ Saltada: {str(e)[:300]}")
    log(f"\n🎉 {len(hechos)} vídeo(s) en {time.time() - t0:.0f}s → carpeta 'salida'. "
        f"Sube video.mp4 y pega publicacion.txt")
    if hechos and sys.platform == "win32":
        os.startfile(str(SALIDA))


if __name__ == "__main__":
    main()
