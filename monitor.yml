"""
Monitor de interrupciones LUMA -> alerta via GitHub Issue.

Logica:
  1. Llama el endpoint de MiLUMA.
  2. Compara contra el estado guardado en estado.json.
  3. Si hay sectores NUEVOS dentro de MUNICIPIOS_TARGET, imprime una alerta.
  4. Guarda el estado nuevo.

El workflow de GitHub Actions se encarga de: correr esto cada X minutos,
abrir el Issue si hay alerta, y hacer commit del estado.

IMPORTANTE: el endpoint no es oficial y su estructura puede cambiar.
La primera corrida guarda la respuesta cruda en muestra_api.json
para que puedas inspeccionar la forma real del JSON y ajustar
la funcion extraer_sectores() si hace falta.
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

# Los 78 municipios de Puerto Rico.
#
# COMO USAR ESTA LISTA:
#   Para IGNORAR un municipio, ponle un # al principio de su linea.
#   Ejemplo:  # "ADJUNTAS",     <- este ya no genera alertas
#
#   Mientras mas municipios activos, mas alertas. Los 78 activos
#   equivale a no filtrar nada: vas a recibir decenas de avisos al dia.
#   Corre asi una semana para medir el ruido real, y luego comenta
#   los que no te sirvan.
#
# MAYUSCULAS sin acentos; el script normaliza.
MUNICIPIOS_TARGET = [
    "ADJUNTAS",
    "AGUADA",
    "AGUADILLA",
    "AGUAS BUENAS",
    "AIBONITO",
    "ANASCO",
    "ARECIBO",
    "ARROYO",
    "BARCELONETA",
    "BARRANQUITAS",
    "BAYAMON",
    "CABO ROJO",
    "CAGUAS",
    "CAMUY",
    "CANOVANAS",
    "CAROLINA",
    "CATANO",
    "CAYEY",
    "CEIBA",
    "CIALES",
    "CIDRA",
    "COAMO",
    "COMERIO",
    "COROZAL",
    "CULEBRA",
    "DORADO",
    "FAJARDO",
    "FLORIDA",
    "GUANICA",
    "GUAYAMA",
    "GUAYANILLA",
    "GUAYNABO",
    "GURABO",
    "HATILLO",
    "HORMIGUEROS",
    "HUMACAO",
    "ISABELA",
    "JAYUYA",
    "JUANA DIAZ",
    "JUNCOS",
    "LAJAS",
    "LARES",
    "LAS MARIAS",
    "LAS PIEDRAS",
    "LOIZA",
    "LUQUILLO",
    "MANATI",
    "MARICAO",
    "MAUNABO",
    "MAYAGUEZ",
    "MOCA",
    "MOROVIS",
    "NAGUABO",
    "NARANJITO",
    "OROCOVIS",
    "PATILLAS",
    "PENUELAS",
    "PONCE",
    "QUEBRADILLAS",
    "RINCON",
    "RIO GRANDE",
    "SABANA GRANDE",
    "SALINAS",
    "SAN GERMAN",
    "SAN JUAN",
    "SAN LORENZO",
    "SAN SEBASTIAN",
    "SANTA ISABEL",
    "TOA ALTA",
    "TOA BAJA",
    "TRUJILLO ALTO",
    "UTUADO",
    "VEGA ALTA",
    "VEGA BAJA",
    "VIEQUES",
    "VILLALBA",
    "YABUCOA",
    "YAUCO",
]

# Cuantos sectores nuevos hacen falta para que valga la pena avisarte.
# Con los 78 municipios activos, 1 te ahoga en alertas. Empezamos en 5.
# Sube este numero si te siguen llegando demasiadas.
UMBRAL_SECTORES_NUEVOS = 5

TIMEOUT = 30
DIR = pathlib.Path(__file__).parent
ARCHIVO_ESTADO = DIR / "estado.json"
ARCHIVO_MUESTRA = DIR / "muestra_api.json"

# ---------------------------------------------------------------


def normalizar(texto):
    """
    Quita TODOS los acentos y normaliza espacios, para que
    'Mayaguez', 'Mayagüez' y 'MAYAGÜEZ' se comparen igual.

    Usa unicodedata en vez de una lista manual de reemplazos:
    asi cubre tildes, dieresis (u de Mayaguez) y la enie sin
    que se nos olvide ningun caso.
    """
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


def extraer_sectores(data):
    """
    Recorre el JSON y saca una lista de (municipio, sector).

    Como no conocemos la forma exacta del JSON de antemano, esta funcion
    recorre la estructura buscando cualquier diccionario que tenga algo
    que parezca un municipio y algo que parezca un sector. Es defensiva
    a proposito. Cuando veas muestra_api.json, puedes reescribirla
    de forma directa y mucho mas corta.
    """
    encontrados = set()

    claves_municipio = ("municipality", "municipio", "town", "pueblo", "region")
    claves_sector = ("sector", "sectorName", "name", "barrio", "nombre")

    def buscar(nodo, municipio_heredado=None):
        if isinstance(nodo, dict):
            municipio = municipio_heredado
            for clave in claves_municipio:
                if clave in nodo and isinstance(nodo[clave], (str, int)):
                    municipio = normalizar(nodo[clave])
                    break

            for clave in claves_sector:
                if clave in nodo and isinstance(nodo[clave], str):
                    encontrados.add((municipio or "SIN MUNICIPIO",
                                     normalizar(nodo[clave])))
                    break

            for valor in nodo.values():
                buscar(valor, municipio)

        elif isinstance(nodo, list):
            for item in nodo:
                buscar(item, municipio_heredado)

    buscar(data)
    return encontrados


def filtrar_target(sectores):
    if not MUNICIPIOS_TARGET:
        return sectores
    objetivo = {normalizar(m) for m in MUNICIPIOS_TARGET}
    return {(mun, sec) for mun, sec in sectores if mun in objetivo}


def cargar_estado():
    if not ARCHIVO_ESTADO.exists():
        return set()
    try:
        crudo = json.loads(ARCHIVO_ESTADO.read_text())
        return {tuple(par) for par in crudo.get("sectores", [])}
    except (json.JSONDecodeError, AttributeError):
        return set()


def guardar_estado(sectores):
    ARCHIVO_ESTADO.write_text(
        json.dumps(
            {"sectores": sorted([list(s) for s in sectores])},
            indent=2,
            ensure_ascii=False,
        )
    )


def escribir_salida(clave, valor):
    """Pasa datos al workflow de GitHub Actions."""
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
        sys.exit(0)  # no rompemos el workflow por un fallo temporal del API

    if not ARCHIVO_MUESTRA.exists():
        ARCHIVO_MUESTRA.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        print("Guardada muestra_api.json -- revisala para afinar extraer_sectores()")

    actuales = filtrar_target(extraer_sectores(data))
    anteriores = cargar_estado()

    nuevos = actuales - anteriores
    restaurados = anteriores - actuales

    print(f"Sectores afectados ahora: {len(actuales)}")
    print(f"Nuevos: {len(nuevos)} | Restaurados: {len(restaurados)}")

    guardar_estado(actuales)

    if len(nuevos) >= UMBRAL_SECTORES_NUEVOS:
        lineas = [f"- **{mun}** — {sec}" for mun, sec in sorted(nuevos)]
        cuerpo = (
            f"Se detectaron {len(nuevos)} sector(es) nuevo(s) sin servicio.\n\n"
            + "\n".join(lineas)
            + f"\n\nTotal de sectores afectados en tus municipios: {len(actuales)}\n\n"
            + "Fuente: mapa de interrupciones de MiLUMA (endpoint no oficial)."
        )
        escribir_salida("hay_alerta", "true")
        escribir_salida("titulo", f"Apagon: {len(nuevos)} sector(es) nuevo(s)")
        escribir_salida("cuerpo", cuerpo)
    else:
        escribir_salida("hay_alerta", "false")


if __name__ == "__main__":
    main()
