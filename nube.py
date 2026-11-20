#!/usr/bin/env python3
"""
VÍDEOS DE POLÍTICA EN LA NUBE (GRATIS) — sin encender tu PC
=============================================================
GitHub Actions (gratis) ejecuta esto cada hora. Cuando falta poco para una hora de publicación:
  1. lee las noticias de ESE MOMENTO, elige la mejor y hace el vídeo (IA + voz + subtítulos)
  2. lo cuelga en tu web gratuita de GitHub Pages (Buffer necesita un enlace público al vídeo)
  3. lo programa en tu calendario de Buffer (TikTok + Instagram Reels) a esa hora exacta
  4. Buffer lo publica aunque tu ordenador esté apagado.

Órdenes (para probar):
  python nube.py              -> modo automático (lo que hace GitHub cada hora)
  python nube.py --canales    -> comprueba la clave de Buffer y enseña tus redes conectadas
  python nube.py --toca       -> solo dice si ahora toca hacer vídeo (lo usa GitHub)
Variables (se cambian en .github/workflows/videos.yml): BUFFER_API_KEY, HORARIOS, REDES, MODO...
"""
import json, os, re, shutil, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ======================= AJUSTES =======================
HORARIOS = [h.strip().zfill(5) for h in os.environ.get("HORARIOS", "09:00,14:00,21:00").split(",") if ":" in h]
ZONA = os.environ.get("ZONA", "Europe/Madrid")
REDES = [r.strip().lower() for r in os.environ.get("REDES", "tiktok,instagram").split(",") if r.strip()]
ANTELACION_MIN = int(os.environ.get("ANTELACION_MIN", "110"))   # hace el vídeo como mucho X min antes
RETRASO_MAX_MIN = int(os.environ.get("RETRASO_MAX_MIN", "40"))  # si GitHub va con retraso, publica igual
AVISOS = os.environ.get("AVISOS", "aviso").strip().lower()   # aviso | rojo | none
SI = ("si", "sí", "true", "1", "yes")
IA_INSTAGRAM = os.environ.get("ETIQUETA_IA_INSTAGRAM", "si").lower() in SI
IA_TIKTOK = os.environ.get("ETIQUETA_IA_TIKTOK", "no").lower() in SI
MODELO_IA = os.environ.get("MODELO_IA", "qwen2.5:7b")
MODO = os.environ.get("MODO", "auto").strip().lower() or "auto"  # auto | ahora | borrador
API = os.environ.get("BUFFER_API_URL", "https://api.buffer.com")
RAMA = "videos"                  # rama de GitHub donde se guardan los vídeos (y la web de Pages)
AQUI = Path(__file__).resolve().parent
PUB = AQUI / "pub"               # copia local de esa rama
ESTADO = PUB / "estado.json"
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass


def log(m):
    print(m, flush=True)


def resumen(m):
    """Lo que escribas aquí sale bonito en la página de la ejecución de GitHub."""
    log(m)
    f = os.environ.get("GITHUB_STEP_SUMMARY")
    if f:
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(m + "\n\n")


def zona():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(ZONA)
    except Exception:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "tzdata"], check=False)
        from zoneinfo import ZoneInfo
        return ZoneInfo(ZONA)


def utc_iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ======================= ESTADO (en la rama 'videos') =======================
def git(*a, cwd=AQUI, env=None, check=True):
    r = subprocess.run(["git", *a], cwd=cwd, env=env, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a[:2])}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def bajar_pub():
    """Trae la rama 'videos' (estado + vídeos pendientes). Si no existe, empieza de cero."""
    shutil.rmtree(PUB, ignore_errors=True)
    PUB.mkdir()
    if subprocess.run(["git", "fetch", "-q", "--depth=1", "origin", RAMA], cwd=AQUI,
                      capture_output=True).returncode == 0:
        subprocess.run(f'git archive FETCH_HEAD | tar -x -C "{PUB}"', shell=True, cwd=AQUI, check=True)


def subir_pub(msg):
    """Publica la carpeta pub/ como la rama 'videos' (un solo commit, así el repo no engorda)."""
    (PUB / ".nojekyll").write_text("")
    filas = "".join(f'<li><a href="v/{p.name}">{p.name}</a></li>' for p in sorted((PUB / "v").glob("*.mp4")))
    (PUB / "index.html").write_text(f"<!doctype html><meta charset=utf-8><title>Vídeos</title>"
                                    f"<h1>Vídeos pendientes de publicar</h1><ul>{filas}</ul>", encoding="utf-8")
    gitdir = git("rev-parse", "--absolute-git-dir")
    env = dict(os.environ, GIT_INDEX_FILE=str(Path(gitdir) / "index_pub"),
               GIT_AUTHOR_NAME="robot-videos", GIT_AUTHOR_EMAIL="robot@users.noreply.github.com",
               GIT_COMMITTER_NAME="robot-videos", GIT_COMMITTER_EMAIL="robot@users.noreply.github.com")
    Path(env["GIT_INDEX_FILE"]).unlink(missing_ok=True)
    base = ["--git-dir", gitdir, "--work-tree", str(PUB)]
    git(*base, "add", "-A", "-f", ".", cwd=PUB, env=env)
    arbol = git(*base, "write-tree", cwd=PUB, env=env)
    commit = git("commit-tree", arbol, "-m", msg, env=env)
    git("push", "-f", "-q", "origin", f"{commit}:refs/heads/{RAMA}")


def cargar_estado():
    try:
        e = json.loads(ESTADO.read_text(encoding="utf-8"))
    except Exception:
        e = {}
    e.setdefault("huecos", {})
    return e


def guardar_estado(e):
    ESTADO.write_text(json.dumps(e, ensure_ascii=False, indent=1), encoding="utf-8")


def limpiar(e, ahora):
    """Borra vídeos y registros de hace más de 3 días (Buffer ya los habrá publicado)."""
    limite = ahora - timedelta(days=3)
    for k in list(e["huecos"]):
        try:
            viejo = datetime.fromisoformat(k) < limite
        except ValueError:
            viejo = True
        if viejo:
            v = e["huecos"].pop(k).get("video")
            if v:
                (PUB / "v" / v).unlink(missing_ok=True)


# ======================= ¿TOCA HACER VÍDEO? =======================
def redes_pendientes(reg):
    return [r for r in REDES if not (reg.get("posts", {}).get(r) or {}).get("ok")]


def hueco_que_toca(e, ahora):
    """La próxima hora de publicación que está a menos de ANTELACION_MIN minutos (o que acaba de pasar)."""
    tz = zona()
    local = ahora.astimezone(tz)
    for d in (-1, 0, 1):
        dia = (local + timedelta(days=d)).date()
        for h in sorted(HORARIOS):
            hh, mm = map(int, h.split(":"))
            t = datetime(dia.year, dia.month, dia.day, hh, mm, tzinfo=tz)
            falta = (t - local).total_seconds() / 60
            if not (-RETRASO_MAX_MIN <= falta <= ANTELACION_MIN):
                continue
            reg = e["huecos"].get(t.isoformat())
            if reg is None or (redes_pendientes(reg) and reg.get("intentos", 0) < 3):
                return t
    return None


def huecos_perdidos(e, ahora):
    """Horas de publicacion que YA pasaron y nunca quedaron programadas.

    Es el aviso que faltaba: si el cron se salta una carrera, o Buffer rechazo el
    post y se gastaron los 3 intentos, antes no se enteraba nadie y al dia siguiente
    nobody decia nada. Aqui marcamos cada hueco una sola vez (campo "avisado") para
    no repetir el aviso cada hora.
    """
    tz = zona()
    local = ahora.astimezone(tz)
    if not e.get("huecos"):
        return []            # estado recien creado: no avisamos de huecos de antes de existir
    faltan, hoy = [], local.date()
    for d in (0, -1):
        dia = local + timedelta(days=d)
        if dia.date() != hoy and d == -1 and not any(k[:10] == str(dia.date()) for k in e["huecos"]):
            continue         # de ayer solo si ayer hubo actividad (evita avisos falsos al instalar)
        for h in sorted(HORARIOS):
            hh, mm = map(int, h.split(":"))
            t = datetime(dia.year, dia.month, dia.day, hh, mm, tzinfo=tz)
            if t >= local - timedelta(minutes=RETRASO_MAX_MIN):
                continue                      # aun esta a tiempo
            if local - t > timedelta(hours=30):
                continue
            reg = e["huecos"].get(t.isoformat())
            if reg is None or redes_pendientes(reg):
                if not (reg or {}).get("avisado"):
                    faltan.append((t, "no se llego a hacer el video" if reg is None
                                   else "el video se hizo pero Buffer no lo tiene: "
                                        + "; ".join(f"{r}: {(p or {}).get('error') or 'sin enviar'}"
                                                    for r, p in (reg.get("posts") or {}).items())))
    return faltan


def avisar_perdidos():
    """Lo llama el paso 'Toca video ahora'. Devuelve True si hay que poner la carrera en rojo."""
    if AVISOS == "none":
        return False
    ahora = datetime.now(timezone.utc)
    tz = zona()
    e = cargar_estado()
    perdidos = huecos_perdidos(e, ahora)
    if not perdidos:
        return False
    lineas = [f"{t.astimezone(tz):%d/%m %H:%M}: {mot}" for t, mot in perdidos]
    for t, _ in perdidos:
        reg = e["huecos"].setdefault(t.isoformat(), {"posts": {}, "intentos": 0})
        reg["avisado"] = True
    guardar_estado(e)
    if os.environ.get("GITHUB_ACTIONS"):
        try:
            subir_pub(f"aviso: {len(perdidos)} hora(s) sin programar")
        except Exception as ex:
            log(f"(no pude guardar el aviso: {ex})")
    texto = ("Se ha(n) perdido " + str(len(perdidos)) + " hora(s) de publicacion: " + " | ".join(lineas)
             + "  ->  revisa la ultima carrera en verde (Actions) y Buffer (publish.buffer.com/queue).")
    log("::warning::" + texto)
    resumen("### ⚠️ Horas que se han perdido\n" + "\n".join(f"- {l}" for l in lineas)
            + "\n\nSi se repite, pon `AVISOS: rojo` en el `env:` de videos.yml para que la carrera "
              "salga en rojo y GitHub te mande un correo.")
    return AVISOS == "rojo"


# ======================= BUFFER =======================
def buffer(query, variables=None, reintentos=3):
    """reintentos=1 para lo que sea solo comprobacion: si Buffer esta caido no queremos
    perder un minuto durmiendo entre reintentos."""
    clave = os.environ.get("BUFFER_API_KEY", "").strip()
    if not clave:
        raise RuntimeError("Falta la clave de Buffer: GitHub → Settings → Secrets → Actions → BUFFER_API_KEY")
    # Si el paso de 'pip install' no llego a ejecutarse (el runner va justo o pip fallo),
    # aqui caemos a urllib de la libreria estandar: Buffer se puede consultar SIN requests.
    try:
        import requests
    except ImportError:
        requests = None
    import urllib.error, urllib.request
    EXC = (requests.RequestException,) if requests else (urllib.error.URLError, OSError)

    def llamar(payload):
        cab = {"Authorization": f"Bearer {clave}", "Content-Type": "application/json"}
        if requests:
            return requests.post(API, timeout=60, json=payload, headers=cab)
        req = urllib.request.Request(API, data=json.dumps(payload).encode(), headers=cab, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as f:
                cuerpo, codigo = f.read().decode(errors="ignore"), f.status
        except urllib.error.HTTPError as ex:
            cuerpo, codigo = ex.read().decode(errors="ignore"), ex.code
        return type("Resp", (), {"status_code": codigo,
                                 "json": lambda self=None: json.loads(cuerpo or "{}")})()

    for intento in range(max(1, reintentos)):
        try:
            r = llamar({"query": query, "variables": variables or {}})
        except EXC as ex:
            if intento == max(1, reintentos) - 1:
                raise RuntimeError(f"No conecto con Buffer: {ex}")
            time.sleep(10)
            continue
        if r.status_code in (401, 403):
            raise RuntimeError("Buffer rechaza la clave (BUFFER_API_KEY). Crea otra en publish.buffer.com/settings/api")
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(20)
            continue
        j = r.json()
        if j.get("errors"):
            raise RuntimeError("Buffer: " + "; ".join(x.get("message", "") for x in j["errors"])[:300])
        return j["data"]
    raise RuntimeError("Buffer no responde (demasiadas peticiones o caído)")


# Buffer ha cambiado algunas veces como llama a cada red en el campo "service"
# (tiktok -> tiktok_business, instagram -> instagram_business...). Si eso vuelve a pasar,
# el filtro viejo se quedaba sin canales y el robot hacia el video SIN programar nada, que
# es exactamente el sintoma de "lo hace pero no lo manda a Buffer". CANON acepta los dos
# nombres y, si aun asi no cuadra, el log dice CUALES son los que devuelve Buffer.
CANON = {"tiktok": "tiktok", "tiktok_business": "tiktok", "tiktok_global": "tiktok",
         "instagram": "instagram", "instagram_business": "instagram",
         "facebook": "facebook", "facebook_page": "facebook", "twitter": "twitter",
         "x": "twitter", "linkedin": "linkedin", "youtube": "youtube",
         "gmb": "gmb", "pinterest": "pinterest"}


def canon(serv):
    b = str(serv or "").strip().lower().replace("-", "_")
    return CANON.get(b, b)


def canales_buffer():
    orgs = buffer("query { account { organizations { id name } } }")["account"]["organizations"]
    todos = []
    for o in orgs:
        q = "query { channels(input: {organizationId: %s}) { id name displayName service isQueuePaused } }" \
            % json.dumps(o["id"])
        for c in (buffer(q)["channels"] or []):
            c["red"] = canon(c.get("service"))
            todos.append(c)
    return todos


CREAR = """mutation Crear($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id dueAt status } }
    ... on MutationError { message }
  }
}"""


def programar(canal, url, texto, cuando, modo):
    red = canal.get("red") or canon(canal.get("service"))
    entrada = {
        "text": texto[:2150],
        "channelId": canal["id"],
        "schedulingType": "automatic",
        "mode": {"ahora": "shareNow", "borrador": "addToQueue"}.get(modo, "customScheduled"),
        "assets": [{"video": {"url": url, "metadata": {"thumbnailOffset": 1500}}}],
        "aiAssisted": True,
    }
    if modo == "borrador":
        entrada["saveToDraft"] = True
    if entrada["mode"] == "customScheduled":
        entrada["dueAt"] = utc_iso(cuando)
    if red == "instagram":
        entrada["metadata"] = {"instagram": {"type": "reel", "shouldShareToFeed": True, "isAiGenerated": IA_INSTAGRAM}}
    elif red == "tiktok":
        entrada["metadata"] = {"tiktok": {"isAiGenerated": IA_TIKTOK}}
    res = buffer(CREAR, {"input": entrada})["createPost"]
    if res.get("post"):
        return res["post"]
    raise RuntimeError(res.get("message") or "Buffer no creó la publicación")


def revisar_publicados(e):
    """Comprueba en Buffer qué pasó con lo que programamos.

    Devuelve (malos, rescatados):
      - "malos": posts que Buffer intentó publicar y fallaron.
      - "rescatados": posts que el estado daba por programados pero que en Buffer
        YA NO EXISTEN (los borraste, o se desconectó el canal y Buffer tiró la cola).
        Sin esto el hueco se queda marcado como "hecho" para siempre: el robot hace
        el vídeo, nadie lo programa y la ejecución sale en verde. Al rescatarlo le
        devolvemos los intentos, así que se vuelve a programar si aún está a tiempo.
    """
    malos, rescatados = [], []
    ahora = datetime.now(timezone.utc)
    limite = int(os.environ.get("CHEQUEOS_MAX", "8"))
    hechos = 0
    for k, reg in sorted(e["huecos"].items(), reverse=True):
        for red, p in (reg.get("posts") or {}).items():
            if not (p and p.get("ok") and p.get("id")):
                continue
            if hechos >= limite:
                return malos, rescatados
            try:
                fecha = datetime.fromisoformat(k)
            except ValueError:
                continue                      # clave de MODO=ahora/borrador: no es una fecha
            if fecha > ahora + timedelta(days=1):
                continue                       # muy lejano, no gastes llamadas
            hechos += 1
            q = 'query { post(input: {id: %s}) { status externalLink error { message } } }' % json.dumps(p["id"])
            try:
                d = buffer(q, reintentos=1).get("post")
            except Exception:
                continue                       # si Buffer no contesta hoy, no tocamos nada
            if not d:                          # <-- antes esto petaba y se tragaba el error
                p.update(ok=False, revisado=False,
                         error="ya no esta en Buffer (lo borraste o el canal se desconecto y Buffer tiro la cola)")
                reg["rescates"] = reg.get("rescates", 0) + 1
                if reg["rescates"] <= 2:
                    reg["intentos"] = 0        # le damos otra oportunidad a este hueco
                    rescatados.append(f"{red} ({k[:16]})")
                continue
            if d.get("status") == "sent":
                p.update(revisado=True, enlace=d.get("externalLink") or "")
            elif d.get("status") == "error":
                p.update(revisado=True, ok=False, error=(d.get("error") or {}).get("message") or "error en Buffer")
                malos.append(f"{red} ({k[:16]}): {p['error']}")
            elif d.get("status") in ("canceled", "cancelled", "deleted"):
                p.update(ok=False, revisado=False, error=f"Buffer marco el post como {d['status']}")
                reg["intentos"] = 0
                rescatados.append(f"{red} ({k[:16]})")
            else:
                p.update(revisado=False, estado=d.get("status"))   # sigue en la cola: lo miraremos otra vez
    return malos, rescatados


# ======================= CREAR EL VÍDEO =======================
def crear_video():
    sys.path.insert(0, str(AQUI))
    import agente_tiktok_v2 as AG
    import static_ffmpeg
    static_ffmpeg.add_paths(weak=True)
    AG.MODELO_IA = MODELO_IA
    AG.HIST = PUB / "historial.json"          # recuerda qué noticias ya se usaron
    AG.SALIDA.mkdir(exist_ok=True)
    log("📰 Leyendo las noticias de ahora mismo...")
    todas = AG.noticias_de_hoy()
    if not todas:
        raise RuntimeError("No hay noticias nuevas en los periódicos")
    log(f"🧠 Eligiendo la mejor de {len(todas)}...")
    for n in AG.elegir(todas, 3):
        try:
            return AG, AG.hacer_video(n)
        except SystemExit as ex:
            raise RuntimeError(str(ex))
        except Exception as ex:
            log(f"   ⚠ Falló con esta noticia ({str(ex)[:150]}), pruebo otra")
    raise RuntimeError("No pude hacer el vídeo con ninguna de las 3 noticias")


def texto_publicacion(info):
    tags = " ".join(info.get("hashtags", []))
    return f"{info.get('descripcion', '').strip()}\n\n{tags}\n\nFuente: {info.get('fuente', '')}".strip()


def url_publica(nombre):
    repo = os.environ.get("GITHUB_REPOSITORY", "usuario/repo")
    dueno, nombre_repo = repo.split("/", 1)
    pages = (f"https://{dueno.lower()}.github.io/" if nombre_repo.lower() == f"{dueno.lower()}.github.io"
             else f"https://{dueno.lower()}.github.io/{nombre_repo}/")
    return pages + f"v/{nombre}", f"https://raw.githubusercontent.com/{repo}/{RAMA}/v/{nombre}"


def esperar_url(nombre, tam):
    """Espera a que GitHub Pages sirva el vídeo (1-3 min). Si Pages no está activado, usa el enlace 'raw'."""
    import requests
    pages, raw = url_publica(nombre)
    fin = time.time() + int(os.environ.get("ESPERA_PAGES", "420"))
    while time.time() < fin:
        try:
            r = requests.head(pages, timeout=20, allow_redirects=False)
            if r.status_code == 200 and int(r.headers.get("content-length", tam)) == tam:
                log(f"🌐 Vídeo en línea: {pages}")
                return pages
        except Exception:
            pass
        time.sleep(15)
    log("⚠ GitHub Pages no responde (¿lo activaste? Settings → Pages → rama 'videos'). Uso el enlace raw.")
    return raw


# ======================= PRINCIPAL =======================
def principal():
    ahora = datetime.now(timezone.utc)
    bajar_pub()
    e = cargar_estado()
    tz = zona()
    # Primero miramos QUEDA ALGO PENDIENTE (aunque Buffer lo diera por hecho y luego lo perdiera),
    # y despues decidimos que hueco toca. Si fuera al reves, un programado que volvio a Buffer
    # nunca se recuperaria: la hora ya habria pasado.
    malos, rescatados = [], []
    if os.environ.get("BUFFER_API_KEY", "").strip():
        try:
            malos, rescatados = revisar_publicados(e)
            if rescatados:
                guardar_estado(e)
                log("♻️ Buffer perdió estos programados y los vuelvo a intentar: " + ", ".join(rescatados))
        except Exception as ex:
            log(f"(no pude revisar publicaciones anteriores: {ex})")
    if MODO == "auto":
        hueco = hueco_que_toca(e, ahora)
        if not hueco:
            log("😴 Ahora no toca ningún vídeo. Próximas horas: " + ", ".join(HORARIOS))
            return 0
        modo = "programar" if hueco > ahora + timedelta(minutes=12) else "ahora"
    else:
        hueco, modo = ahora.astimezone(tz).replace(second=0, microsecond=0), MODO
    clave = hueco.isoformat() if MODO == "auto" else f"{hueco.isoformat()}-{MODO}"
    reg = e["huecos"].get(clave) or {"posts": {}, "intentos": 0}

    # 0. Comprobar Buffer ANTES de gastar tiempo en el vídeo
    local_sin_clave = not os.environ.get("BUFFER_API_KEY") and not os.environ.get("GITHUB_ACTIONS")
    todos = [] if local_sin_clave else canales_buffer()
    if not local_sin_clave:
        # Una linea que responde a "¿ve Buffer mi canal?" en CADA carrera que hace un video
        log("📡 Buffer: " + (", ".join(f"{c.get('red') or c.get('service')}="
                                     f"{c.get('displayName') or c.get('name')}"
                                     + ("(cola EN PAUSA)" if c.get("isQueuePaused") else "")
                                     for c in todos) or "la cuenta no tiene NI UN canal conectado"))
    canales = [c for c in todos if c.get("red") in REDES]

    # (la revision de lo anterior ya se hizo arriba, antes de elegir hueco)

    # 1. Vídeo (si no se hizo ya en un intento anterior)
    if not reg.get("url"):
        AG, carpeta = crear_video()
        info = json.loads((carpeta / "info.json").read_text(encoding="utf-8"))
        nombre = f"{hueco.astimezone(tz):%Y%m%d_%H%M}_{AG.slug(info['titulo'])[:40]}.mp4"
        (PUB / "v").mkdir(exist_ok=True)
        shutil.copy(carpeta / "video.mp4", PUB / "v" / nombre)
        reg.update(video=nombre, titulo=info["titulo"], fuente=info.get("fuente", ""),
                   link=info.get("link", ""), texto=texto_publicacion(info))
        limpiar(e, ahora)
        e["huecos"][clave] = reg
        guardar_estado(e)
        if os.environ.get("GITHUB_ACTIONS"):
            subir_pub(f"vídeo para {hueco.astimezone(tz):%d/%m %H:%M}")
            reg["url"] = esperar_url(nombre, (PUB / "v" / nombre).stat().st_size)
        else:
            reg["url"] = f"(local) {PUB / 'v' / nombre}"
        guardar_estado(e)

    # 2. Buffer
    errores = []
    if local_sin_clave:
        log("ℹ Sin BUFFER_API_KEY: no programo nada (prueba local). Vídeo en " + reg["url"])
        return 0
    faltan = [r for r in REDES if r not in {c.get("red") for c in canales}]
    if faltan:
        errores.append(
            f"Buffer no tiene ningun canal que se llame '{', '.join(faltan)}' para esta cuenta. "
            f"Lo que Buffer SI te devuelve es: "
            + (", ".join(f"{c.get('service')}={c.get('displayName') or c.get('name')}" for c in todos)
               or "(nada)")
            + "  -> o conectas ese canal en publish.buffer.com, o cambias REDES en videos.yml "
              "(las horas y las redes se editan en el 'env:' del workflow)")
    for c in canales:
        red = c["red"]
        if (reg["posts"].get(red) or {}).get("ok"):
            log(f"   {red}: ya estaba programado para este hueco, no lo repito")
            continue
        try:
            post = programar(c, reg["url"], reg["texto"], hueco, modo)
            reg["posts"][red] = {"ok": True, "id": post["id"], "estado": post.get("status"),
                                 "canal": c.get("name") or c.get("displayName") or ""}
            log(f"✅ {red}: {'en borradores' if modo == 'borrador' else 'programado'} "
                f"({hueco.astimezone(tz):%d/%m %H:%M})")
        except Exception as ex:
            reg["posts"][red] = {"ok": False, "error": str(ex)[:300]}
            errores.append(f"{red}: {str(ex)[:300]}")
    reg["intentos"] = reg.get("intentos", 0) + 1
    e["huecos"][clave] = reg
    guardar_estado(e)
    if os.environ.get("GITHUB_ACTIONS"):
        subir_pub(f"estado {datetime.now(tz):%d/%m %H:%M}")

    resumen(f"## 🎬 {reg.get('titulo', '')}\n\n**Hora:** {hueco.astimezone(tz):%d/%m/%Y %H:%M} · "
            f"**Fuente:** {reg.get('fuente', '')}\n\n**Vídeo:** {reg['url']}\n\n"
            + "\n".join(f"- {r}: {'✅' if (p or {}).get('ok') else '❌ ' + (p or {}).get('error', '')}"
                        for r, p in reg["posts"].items()))
    for m in malos:
        errores.append("Buffer no pudo publicar → " + m)
    if rescatados:
        log("::warning::Un programado que el robot daba por bueno ya no estaba en Buffer ("
            + ", ".join(rescatados) + "). Reintentado. Si esto se repite: el canal se desconecta "
            "a menudo, o la cola esta llena (el plan gratis de Buffer admite 10 programados por canal).")
    if errores:
        resumen("### ❌ Problemas\n" + "\n".join(f"- {x}" for x in errores))
        return 1        # la ejecución sale en rojo y GitHub te manda un correo
    return 0


def main():
    if "--canales" in sys.argv:
        try:
            lista = canales_buffer()
        except Exception as ex:
            log(f"(Buffer no contesto: {ex})")
            return 1
        for c in lista:
            marca = "LO USO" if c.get("red") in REDES else "(ignorado: no esta en REDES)"
            log(f"  {str(c['service']):18} -> {c.get('red'):10} "
                f"{(c.get('displayName') or c['name'] or '')[:28]:30} {marca}"
                + ("  ¡COLA EN PAUSA!" if c.get("isQueuePaused") else ""))
        log(f"  (REDES = {','.join(REDES)}; si un canal sale '(ignorado)' conecta ese canal "
            f"en Buffer o añade su nombre a REDES en el workflow)")
        return 0
    if "--toca" in sys.argv:
        bajar_pub()
        ahora = datetime.now(timezone.utc)
        tz = zona()
        local = ahora.astimezone(tz)
        est = cargar_estado()
        if MODO == "auto" and os.environ.get("BUFFER_API_KEY", "").strip():
            # Antes de decir "no toca" comprobamos en Buffer que lo dado por bueno lo este de
            # verdad. Si un programado volo (lo borraste, o el canal se desconecto y Buffer tiro
            # la cola), ese hueco vuelve a estar pendiente y este paso dira SÍ en vez de callarse.
            try:
                _, resc = revisar_publicados(est)
                if resc:
                    log("♻️ en Buffer ya no estan: " + ", ".join(resc) + " -> se reintentan")
            except Exception as ex:
                log(f"(no pude revisar Buffer en este paso: {ex})")
        hueco = None if MODO != "auto" else hueco_que_toca(est, ahora)
        si = MODO != "auto" or hueco is not None
        log("¿Toca vídeo ahora? " + ("SÍ" if si else "no") + f"   (MODO={MODO}, hora del runner: "
            f"{local:%H:%M:%S} de {tz})")
        if not si:
            # Por que no: la proxima hora de publicacion y cuanto falta para ella. Si esto sale
            # "no toca" todos los dias, el problema son las HORARIOS/zona, no Buffer.
            esper = []
            for d in (0, 1):
                for h in sorted(HORARIOS):
                    hh, mm = map(int, h.split(":"))
                    t = datetime((local + timedelta(days=d)).year, (local + timedelta(days=d)).month,
                                 (local + timedelta(days=d)).day, hh, mm, tzinfo=tz)
                    if t > local:
                        esper.append((t, (t - local).total_seconds() / 60))
            if esper:
                t, f = sorted(esper)[0]
                log(f"   la siguiente es {t:%H:%M} (faltan {f:.0f} min; esta hace video entre "
                    f"{ANTELACION_MIN} min antes y {RETRASO_MAX_MIN} min despues)")
                if f > ANTELACION_MIN:
                    log(f"   por eso hoy no toca: habria que lanzarlo a las "
                        f"{(t - timedelta(minutes=ANTELACION_MIN)):%H:%M} o mas tarde. "
                        f"Si el repo es publico y el cron es '23 * * * *', el runner puede "
                        f"retrasarse; sube ANTELACION_MIN (p.ej. 170) en el 'env:' de videos.yml")
            e0 = cargar_estado()
            hechos = sorted(k for k in e0["huecos"] if k[:10] == local.isoformat()[:10])
            log(f"   huecos de hoy ya en el estado: {len(hechos)}"
                + ("" if hechos else " (ninguno: el robot no ha hecho ninguno hoy)"))
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as fh:
                fh.write(f"toca={'si' if si else 'no'}\n")
        if not si:
            # Solo miramos las horas perdidas cuando NO toca hacer nada (si toca, ya las
            # recuperar principal al ver el hueco pendiente).
            try:
                if avisar_perdidos():
                    return 1
            except Exception as ex:
                log(f"(no pude comprobar las horas perdidas: {ex})")
        return 0
    try:
        return principal()
    except Exception as ex:
        resumen(f"### ❌ Error\n{ex}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
