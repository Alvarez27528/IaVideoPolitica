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


# ======================= BUFFER =======================
def buffer(query, variables=None):
    clave = os.environ.get("BUFFER_API_KEY", "").strip()
    if not clave:
        raise RuntimeError("Falta la clave de Buffer: GitHub → Settings → Secrets → Actions → BUFFER_API_KEY")
    import requests
    for intento in range(3):
        try:
            r = requests.post(API, timeout=60, json={"query": query, "variables": variables or {}},
                              headers={"Authorization": f"Bearer {clave}", "Content-Type": "application/json"})
        except requests.RequestException as ex:
            if intento == 2:
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


def canales_buffer():
    orgs = buffer("query { account { organizations { id name } } }")["account"]["organizations"]
    todos = []
    for o in orgs:
        q = "query { channels(input: {organizationId: %s}) { id name displayName service isQueuePaused } }" \
            % json.dumps(o["id"])
        todos += buffer(q)["channels"]
    return todos


CREAR = """mutation Crear($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id dueAt status } }
    ... on MutationError { message }
  }
}"""


def programar(canal, url, texto, cuando, modo):
    red = canal["service"]
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
    """Mira si las publicaciones de las últimas horas salieron bien en Buffer (para avisarte si no)."""
    malos = []
    ahora = datetime.now(timezone.utc)
    for k, reg in e["huecos"].items():
        for red, p in (reg.get("posts") or {}).items():
            if not (p and p.get("ok") and p.get("id")) or p.get("revisado"):
                continue
            try:
                if datetime.fromisoformat(k) > ahora - timedelta(minutes=20):
                    continue
                q = 'query { post(input: {id: %s}) { status externalLink error { message } } }' % json.dumps(p["id"])
                d = buffer(q)["post"]
            except Exception:
                continue
            if d["status"] == "sent":
                p.update(revisado=True, enlace=d.get("externalLink") or "")
            elif d["status"] == "error":
                p.update(revisado=True, ok=False, error=(d.get("error") or {}).get("message") or "error en Buffer")
                malos.append(f"{red} ({k[:16]}): {p['error']}")
    return malos


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
    canales = [] if local_sin_clave else [c for c in canales_buffer() if c["service"] in REDES]

    # ¿Salieron bien las anteriores?
    malos = []
    try:
        malos = [] if local_sin_clave else revisar_publicados(e)
    except Exception as ex:
        log(f"(no pude revisar publicaciones anteriores: {ex})")

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
    faltan = [r for r in REDES if r not in {c["service"] for c in canales}]
    if faltan:
        errores.append(f"No tienes conectado en Buffer: {', '.join(faltan)}")
    for c in canales:
        if (reg["posts"].get(c["service"]) or {}).get("ok"):
            continue
        try:
            post = programar(c, reg["url"], reg["texto"], hueco, modo)
            reg["posts"][c["service"]] = {"ok": True, "id": post["id"], "estado": post.get("status")}
            log(f"✅ {c['service']}: {'en borradores' if modo == 'borrador' else 'programado'} "
                f"({hueco.astimezone(tz):%d/%m %H:%M})")
        except Exception as ex:
            reg["posts"][c["service"]] = {"ok": False, "error": str(ex)[:300]}
            errores.append(f"{c['service']}: {str(ex)[:300]}")
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
    if errores:
        resumen("### ❌ Problemas\n" + "\n".join(f"- {x}" for x in errores))
        return 1        # la ejecución sale en rojo y GitHub te manda un correo
    return 0


def main():
    if "--canales" in sys.argv:
        for c in canales_buffer():
            log(f"  {c['service']:10} {c.get('displayName') or c['name']:30} id={c['id']}"
                + ("  (cola en PAUSA)" if c.get("isQueuePaused") else ""))
        return 0
    if "--toca" in sys.argv:
        bajar_pub()
        si = MODO != "auto" or hueco_que_toca(cargar_estado(), datetime.now(timezone.utc)) is not None
        log("¿Toca vídeo ahora? " + ("SÍ" if si else "no"))
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as fh:
                fh.write(f"toca={'si' if si else 'no'}\n")
        return 0
    try:
        return principal()
    except Exception as ex:
        resumen(f"### ❌ Error\n{ex}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
