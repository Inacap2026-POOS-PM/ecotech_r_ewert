import os
from pathlib import Path

from dotenv import load_dotenv

RUTA_RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RUTA_RAIZ / ".env")


class ErrorDeConfiguracion(Exception):
    """Falta una variable del .env o su valor no es seguro."""


def requerir(*nombres):
    """Devuelve las variables pedidas o informa todas las que faltan juntas."""
    valores = {}
    faltantes = []
    for nombre in nombres:
        valor = os.environ.get(nombre, "").strip()
        if valor:
            valores[nombre] = valor
        else:
            faltantes.append(nombre)
    if faltantes:
        raise ErrorDeConfiguracion(
            "Faltan variables en .env: " + ", ".join(faltantes)
            + ". Copia .env.example a .env y complétalas.")
    return valores


def requerir_url_segura(nombre):
    """Exige HTTPS para que el token nunca viaje sin cifrar ni a un servidor falso."""
    url = requerir(nombre)[nombre].rstrip("/")
    if not url.lower().startswith("https://"):
        raise ErrorDeConfiguracion(f"{nombre} debe comenzar con https://.")
    return url
