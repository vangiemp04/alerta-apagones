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
import sys
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


def main():
    try:
        data = llamar_api()
    except Exception as error:
        print(f"Fallo la llamada al API: {error}")
        escribir_salida("hay_alerta", "false")
        sys.exit(0)

    timestamp = data.get("timestamp", "sin fecha")
    actuales = extraer(data)

    if not actuales:
        print("ADVERTENCIA: no se extrajo ninguna region.")
        print("El API pudo haber cambiado de forma. Respuesta cruda:")
        print(json.dumps(data, indent=2)[:1500])
        escribir_salida("hay_alerta", "false")
        sys.exit(0)

    anteriores = cargar_estado()

    print(f"Data de LUMA: {timestamp}")
    print(f"{'Region':<12} {'Averia':>8} {'Planif.':>9} {'Cambio':>8}")
    print("-" * 41)

    isla_averia = 0
    isla_antes = 0
    saltos = []

    for nombre in sorted(actuales):
        ahora = actuales[nombre]["averia"]
        antes = anteriores.get(nombre, {}).get("averia", ahora)
        cambio = ahora - antes

        isla_averia += ahora
        isla_antes += antes

        signo = f"+{cambio}" if cambio > 0 else str(cambio)
        print(
            f"{nombre:<12} {ahora:>8} {actuales[nombre]['planificados']:>9} {signo:>8}"
        )

        if cambio >= UMBRAL_CLIENTES_REGION:
            saltos.append((nombre, antes, ahora, cambio))

    salto_isla = isla_averia - isla_antes
    print("-" * 41)
    print(f"{'ISLA':<12} {isla_averia:>8} {'':>9} {salto_isla:>+8}")

    guardar_estado(actuales, timestamp)
    guardar_historico(actuales, timestamp)

    evento_isla = salto_isla >= UMBRAL_CLIENTES_ISLA

    if not saltos and not evento_isla:
        print("\nSin cambios relevantes. No se alerta.")
        escribir_salida("hay_alerta", "false")
        return

    if evento_isla:
        titulo = f"Apagon mayor: +{salto_isla:,} clientes en la isla"
    else:
        peor = max(saltos, key=lambda x: x[3])
        titulo = f"Apagon en {peor[0]}: +{peor[3]:,} clientes"

    lineas = [
        f"**Data de LUMA:** {timestamp}",
        "",
        f"**Averia real en la isla:** {isla_averia:,} clientes "
        f"({salto_isla:+,} desde la ultima revision)",
        "",
        "| Region | Averia real | Cambio | Planificados |",
        "|---|---:|---:|---:|",
    ]
    for nombre in sorted(actuales):
        ahora = actuales[nombre]["averia"]
        antes = anteriores.get(nombre, {}).get("averia", ahora)
        cambio = ahora - antes
        marca = " **<-**" if any(s[0] == nombre for s in saltos) else ""
        lineas.append(
            f"| {nombre}{marca} | {ahora:,} | {cambio:+,} | "
            f"{actuales[nombre]['planificados']:,} |"
        )

    lineas += [
        "",
        "*Averia real = clientes sin servicio menos los de mantenimiento "
        "programado. Los planificados se excluyen a proposito: los anunciaron "
        "con anticipacion y no son un evento.*",
        "",
        "Fuente: mapa de interrupciones de MiLUMA (endpoint no oficial).",
    ]

    escribir_salida("hay_alerta", "true")
    escribir_salida("titulo", titulo)
    escribir_salida("cuerpo", "\n".join(lineas))
    print(f"\nALERTA: {titulo}")


if __name__ == "__main__":
    main()
