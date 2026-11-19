import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from infraestructura.configuracion import RUTA_RAIZ


class ErrorDeConexion(Exception):
    """No se pudo acceder a la base de datos."""


@contextmanager
def obtener_conexion():
    """Confirma los cambios si todo resulta bien y los deshace si falla."""
    motor = os.environ.get("DB_MOTOR", "sqlite").strip().lower()
    if motor != "sqlite":
        raise ErrorDeConexion("Este proyecto utiliza SQLite. Revisa DB_MOTOR.")
    nombre = os.environ.get("DB_NOMBRE", "").strip()
    if not nombre:
        raise ErrorDeConexion("Falta DB_NOMBRE. Copia .env.example a .env.")
    ruta = Path(nombre)
    if not ruta.is_absolute():
        ruta = RUTA_RAIZ / ruta
    try:
        conn = sqlite3.connect(ruta)
    except sqlite3.OperationalError as error:
        raise ErrorDeConexion("No se pudo abrir la base de datos. Revisa DB_NOMBRE.") from error
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            yield conn
    except sqlite3.OperationalError as error:
        raise ErrorDeConexion(
            "No se pudo completar la operación. Revisa el esquema o si la base está bloqueada."
        ) from error
    finally:
        conn.close()
