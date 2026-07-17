"""
Explorador de endpoints de MiLUMA.

Objetivo: averiguar si LUMA publica data por MUNICIPIO, y no solo
por las 7 regiones operativas que ya usamos.

Esto NO toca el monitor. Se corre a mano desde Actions y guarda
las respuestas crudas en la carpeta exploracion/ para inspeccionarlas.

Uso:
    python explorar.py
"""

import json
import pathlib
import urllib.request

BASE = "https://api.miluma.lumapr.com/miluma-outage-api/outage"

# El que ya usamos (de control, para confirmar que el API responde)
# y el que nunca hemos llamado.
ENDPOINTS = {
    "regiones": f"{BASE}/regionsWithoutService",
    "municipios": f"{BASE}/municipality/towns",
}

DIR = pathlib.Path(__file__).parent / "exploracion"
TIMEOUT = 30


def llamar(url):
    peticion = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (monitor personal de interrupciones)",
        },
    )
    with urllib.request.urlopen(peticion, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def describir(nodo, nivel=0, ruta="raiz"):
    """
    Imprime la FORMA del JSON sin vomitar todo el contenido.
    Esto es lo que nos dice si hay data municipal.
    """
    sangria = "  " * nivel
    if nivel > 4:
        print(f"{sangria}...")
        return

    if isinstance(nodo, dict):
        print(f"{sangria}{ruta}: objeto con {len(nodo)} claves")
        for k, v in list(nodo.items())[:14]:
            if isinstance(v, (dict, list)):
                describir(v, nivel + 1, k)
            else:
                muestra = str(v)[:45]
                print(f"{sangria}  {k} = {muestra}  ({type(v).__name__})")
        if len(nodo) > 14:
            print(f"{sangria}  ... y {len(nodo)-14} claves mas")

    elif isinstance(nodo, list):
        print(f"{sangria}{ruta}: lista de {len(nodo)} elementos")
        if nodo:
            print(f"{sangria}  --- primer elemento ---")
            describir(nodo[0], nivel + 1, "[0]")
            if len(nodo) > 1 and isinstance(nodo[0], (str, int)):
                print(f"{sangria}  muestra: {nodo[:8]}")


def buscar_pistas(data):
    """Busca palabras que delaten data municipal o de sectores."""
    crudo = json.dumps(data).lower()
    # Raices, no palabras completas: "municipality" NO es subcadena
    # de "municipalities". Ese error nos haria creer que no hay data
    # municipal teniendola enfrente.
    pistas = {
        "municipio": ["municipalit", "municipio"],
        "pueblo / town": ["town", "pueblo"],
        "sector / barrio": ["sector", "barrio"],
        "clientes sin servicio": ["withoutservice", "clientswithout"],
        "coordenadas": ["latitud", "longitud", "coordinate", "\"lat\"", "geometry"],
        "planificados": ["planned"],
        "conteos numericos": ["count", "total", "clients"],
    }
    print("\n  Pistas encontradas:")
    for etiqueta, claves in pistas.items():
        hay = [k for k in claves if k in crudo]
        print(f"    {'SI' if hay else 'no'}  {etiqueta:<26} {hay if hay else ''}")


def main():
    DIR.mkdir(exist_ok=True)

    for nombre, url in ENDPOINTS.items():
        print("\n" + "=" * 68)
        print(f"  {nombre.upper()}")
        print(f"  {url}")
        print("=" * 68)

        try:
            data = llamar(url)
        except Exception as e:
            print(f"  FALLO: {e}")
            continue

        crudo = json.dumps(data, indent=2, ensure_ascii=False)
        archivo = DIR / f"{nombre}.json"
        archivo.write_text(crudo)

        print(f"  Respuesta OK — {len(crudo):,} caracteres")
        print(f"  Guardado en: exploracion/{nombre}.json")
        print("\n  Estructura:")
        describir(data)
        buscar_pistas(data)

    print("\n" + "=" * 68)
    print("  Listo. Revisa la carpeta exploracion/ en el repo.")
    print("=" * 68)


if __name__ == "__main__":
    main()
