"""
Monitor de interrupciones LUMA -> alerta via GitHub Issue.

QUE MIDE ESTE SCRIPT
--------------------
El endpoint de MiLUMA devuelve 7 REGIONES operativas (no municipios,
no sectores) con conteos de clientes:

    totalClientsWithoutService          <- todos los que no tienen luz
    totalClientsAffectedByPlannedOutage <- de esos, cuantos son mantenimiento

La resta es lo que nos importa:

    AVERIA REAL = sin servicio - planificados

Un apagon planificado lo anunciaron con semanas de anticipacion.
No es noticia, no mueve a nadie, y si alertas por el te ahogas en ruido.
Un apagon INESPERADO si es un evento.

Este script solo te avisa por averias reales.
"""

import json
import os
import pathlib
import subprocess
import sys
import time
import unicodedata
import urllib.request

# ---------------------------------------------------------------
# CONFIGURACION -- esto es lo unico que editas
# ---------------------------------------------------------------

API_URL = "https://api.miluma.lumapr.com/miluma-outage-api/outage/regionsWithoutService"

# Las 7 regiones de LUMA. Comenta con # las que no te interesen.
# OJO: son regiones operativas, NO municipios. La region "San Juan"
# cubre mucho mas que el municipio de San Juan.
REGIONES_TARGET = [
    "ARECIBO",
    "BAYAMON",
    "CAROLINA",
    "CAGUAS",
    "MAYAGUEZ",
    "PONCE",
    "SAN JUAN",
]

# Cuantos clientes con AVERIA REAL nuevos, en una sola region,
# para que te avise. El baseline normal ronda 100-300 por region.
# 500 = algo se rompio de verdad.
UMBRAL_CLIENTES_REGION = 500

# O si la isla completa sube de golpe (evento mayor / apagon general).
UMBRAL_CLIENTES_ISLA = 2000

# --- Modo continuo ---
# El cron de GitHub es impuntual: */10 en la practica corre cada 20-45 min.
# Solucion: un solo arranque por hora, y el script se queda vivo revisando.
# 55 min para terminar antes de que el proximo cron lo relance.
DURACION_MIN = 55
INTERVALO_SEG = 120

TIMEOUT = 30
DIR = pathlib.Path(__file__).parent
ARCHIVO_ESTADO = DIR / "estado.json"
ARCHIVO_HISTORICO = DIR / "historico.csv"

# ---------------------------------------------------------------


def normalizar(texto):
    """MAYUSCULAS sin acentos, para comparar nombres de regiones."""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.upper().split())


def llamar_api():
    peticion = urllib.request.Request(
        API_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (monitor personal de interrupciones)",
        },
    )
    with urllib.request.urlopen(peticion, timeout=TIMEOUT) as respuesta:
        return json.loads(respuesta.read().decode("utf-8"))


def extraer(data):
    """
    Saca la averia real por region.
    Devuelve: {"SAN JUAN": {"averia": 157, "planificados": 4402, "total": 4559}, ...}
    """
    objetivo = {normalizar(r) for r in REGIONES_TARGET}
    resultado = {}

    for region in data.get("regions", []):
        nombre = normalizar(region.get("name", ""))
        if objetivo and nombre not in objetivo:
            continue

        sin_servicio = int(region.get("totalClientsWithoutService", 0))
        planificados = int(region.get("totalClientsAffectedByPlannedOutage", 0))
        averia = max(0, sin_servicio - planificados)

        resultado[nombre] = {
            "averia": averia,
            "planificados": planificados,
            "total": sin_servicio,
        }

    return resultado


def cargar_estado():
    if not ARCHIVO_ESTADO.exists():
        return {}
    try:
        return json.loads(ARCHIVO_ESTADO.read_text()).get("regiones", {})
    except (json.JSONDecodeError, AttributeError):
        return {}


def guardar_estado(regiones, timestamp):
    ARCHIVO_ESTADO.write_text(
        json.dumps(
            {"timestamp": timestamp, "regiones": regiones},
            indent=2,
            ensure_ascii=False,
        )
    )


def guardar_historico(regiones, timestamp):
    """Una linea por corrida. Con esto afinas los umbrales con data real."""
    nuevo = not ARCHIVO_HISTORICO.exists()
    with open(ARCHIVO_HISTORICO, "a") as f:
        if nuevo:
            f.write("timestamp,region,averia_real,planificados,total\n")
        for nombre, d in sorted(regiones.items()):
            f.write(
                f'"{timestamp}","{nombre}",{d["averia"]},'
                f'{d["planificados"]},{d["total"]}\n'
            )


def escribir_salida(clave, valor):
    ruta = os.environ.get("GITHUB_OUTPUT")
    if not ruta:
        print(f"[salida local] {clave}={valor}")
        return
    with open(ruta, "a") as f:
        if "\n" in str(valor):
            f.write(f"{clave}<<FIN\n{valor}\nFIN\n")
        else:
            f.write(f"{clave}={valor}\n")


def sh(cmd, check=False):
    """Corre un comando. Devuelve (codigo, salida)."""
    p = subprocess.run(cmd, cwd=str(DIR), capture_output=True, text=True)
    if check and p.returncode:
        print(f"  ! {' '.join(cmd)} -> {p.stderr.strip()[:200]}")
    return p.returncode, (p.stdout + p.stderr).strip()


def git_guardar():
    """Commitea el estado. Reintenta rebaseando si el push choca."""
    sh(["git", "add", "estado.json", "historico.csv"])
    if sh(["git", "diff", "--staged", "--quiet"])[0] == 0:
        return  # nada cambio
    sh(["git", "commit", "-m", "Actualizar estado [skip ci]"])
    for _ in range(3):
        if sh(["git", "push"])[0] == 0:
            return
        sh(["git", "pull", "--rebase", "origin", "main"])
        time.sleep(2)
    print("  ! no se pudo hacer push")


def abrir_issue(titulo, cuerpo):
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo or not os.environ.get("GH_TOKEN"):
        print(f"  [local] Issue que se abriria: {titulo}")
        return
    cod, out = sh(["gh", "issue", "create", "--title", titulo,
                   "--body", cuerpo, "--repo", repo], check=True)
    print(f"  Issue abierto: {out.splitlines()[-1] if out else 'ok'}" if not cod
          else "  ! fallo el Issue")


def ciclo(anteriores):
    """Una revision. Devuelve el estado nuevo (o el anterior si fallo)."""
    try:
        data = llamar_api()
    except Exception as error:
        print(f"  ! fallo la llamada al API: {error}")
        return anteriores

    timestamp = data.get("timestamp", "sin fecha")
    actuales = extraer(data)

    if not actuales:
        print("  ! no se extrajo ninguna region -- el API pudo cambiar de forma")
        return anteriores

    isla_averia = sum(d["averia"] for d in actuales.values())
    isla_antes = sum(
        anteriores.get(n, actuales[n])["averia"] for n in actuales
    ) if anteriores else isla_averia

    saltos = []
    for nombre in sorted(actuales):
        ahora = actuales[nombre]["averia"]
        antes = anteriores.get(nombre, {}).get("averia", ahora) if anteriores else ahora
        if ahora - antes >= UMBRAL_CLIENTES_REGION:
            saltos.append((nombre, antes, ahora, ahora - antes))

    salto_isla = isla_averia - isla_antes
    detalle = "  ".join(
        f"{n[:3]}={actuales[n]['averia']}" for n in sorted(actuales)
    )
    print(f"  {timestamp}  isla={isla_averia:<5} ({salto_isla:+})  {detalle}")

    guardar_estado(actuales, timestamp)
    guardar_historico(actuales, timestamp)
    git_guardar()

    evento_isla = salto_isla >= UMBRAL_CLIENTES_ISLA
    if not saltos and not evento_isla:
        return actuales

    if evento_isla:
        titulo = f"Apagon mayor: +{salto_isla:,} clientes en la isla"
    else:
        peor = max(saltos, key=lambda x: x[3])
        titulo = f"Apagon en {peor[0]}: +{peor[3]:,} clientes"

    lineas = [
        f"**Data de LUMA:** {timestamp}",
        "",
        f"**Averia real en la isla:** {isla_averia:,} clientes "
        f"({salto_isla:+,} desde la revision anterior)",
        "",
        "| Region | Averia real | Cambio | Planificados |",
        "|---|---:|---:|---:|",
    ]
    for nombre in sorted(actuales):
        ahora = actuales[nombre]["averia"]
        antes = anteriores.get(nombre, {}).get("averia", ahora) if anteriores else ahora
        marca = " **<-**" if any(s[0] == nombre for s in saltos) else ""
        lineas.append(
            f"| {nombre}{marca} | {ahora:,} | {ahora - antes:+,} | "
            f"{actuales[nombre]['planificados']:,} |"
        )
    lineas += [
        "",
        "*Averia real = clientes sin servicio menos mantenimiento programado.*",
        "",
        "Fuente: mapa de interrupciones de MiLUMA (endpoint no oficial).",
    ]

    print(f"  >> ALERTA: {titulo}")
    abrir_issue(titulo, "\n".join(lineas))
    return actuales


def main():
    # Una sola pasada:  python monitor.py --once
    una_vez = "--once" in sys.argv

    sh(["git", "config", "user.name", "monitor-bot"])
    sh(["git", "config", "user.email", "monitor-bot@users.noreply.github.com"])

    estado = cargar_estado()
    if una_vez:
        ciclo(estado)
        return

    fin = time.time() + DURACION_MIN * 60
    n = 0
    print(f"Modo continuo: revisando cada {INTERVALO_SEG}s por {DURACION_MIN} min")
    print("-" * 60)

    while time.time() < fin:
        n += 1
        print(f"[{n:>2}]", end="")
        estado = ciclo(estado)
        if time.time() + INTERVALO_SEG >= fin:
            break
        time.sleep(INTERVALO_SEG)

    print("-" * 60)
    print(f"Fin. {n} revisiones. El proximo cron relanza en la hora.")


if __name__ == "__main__":
    main()
