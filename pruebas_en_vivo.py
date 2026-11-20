"""Revisión práctica de la E3: ejecuta las pruebas y fuerza cada falla en vivo.

Uso: python pruebas_en_vivo.py

Usa una base temporal, así que no modifica ecotech.db. Necesita internet para los
escenarios con las APIs reales; si no hay red, esos escenarios muestran el plan B o C.
"""

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from infraestructura.configuracion import ErrorDeConfiguracion
from infraestructura.esquema import inicializar_bd
from servicios.cliente_supabase import ClienteSupabase
from servicios.cliente_tipo_cambio import ClienteTipoCambio
from servicios.servicio_tipo_cambio import ServicioTipoCambio
from servicios.sincronizador import SincronizadorEmpleados, describir_antiguedad

URL_CAIDA = "https://servidor-inexistente.ecotech-prueba.cl/v6/latest"
VARIABLES_SUPABASE = ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_EMAIL", "SUPABASE_PASSWORD")


def titulo(numero, texto, criterio):
    """Encabezado de cada escenario con el criterio de la rúbrica que demuestra."""
    print(f"\n{'=' * 72}\n{numero}. {texto}\n   Criterio: {criterio}\n{'-' * 72}")


def mostrar_tipo_cambio(resultado):
    """Imprime el resultado igual que la opción 12 del menú."""
    print(f"   Origen de los datos: {resultado.origen.upper()}")
    if resultado.motivo:
        print(f"   Motivo: {resultado.motivo}")
    if resultado.tipo_cambio:
        print(f"   {resultado.tipo_cambio.resumen()}")
        print(f"   Obtenido {describir_antiguedad(resultado.obtenido_en)}")
        print(f"   US$ {resultado.monto:,.2f} = $ {resultado.total():,.0f} pesos chilenos")
    else:
        print("   No hay un tipo de cambio guardado: se informa sin caerse (plan C).")


def ejecutar_pruebas_unitarias():
    """Corre las pruebas automáticas dentro del mismo proceso."""
    raiz = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(raiz / "pruebas"), top_level_dir=None)
    resultado = unittest.TextTestRunner(verbosity=1, stream=sys.stdout).run(suite)
    return resultado.wasSuccessful()


def main():
    """Ejecuta los escenarios en orden y resume el resultado."""
    print("EcoTech · Revisión práctica de pruebas (E3)")
    titulo(1, "Pruebas automáticas (sin red, con respuestas simuladas)",
           "todos: timeout, códigos 401/403/404/429/5xx, JSON hostil, bcrypt, planes B y C")
    pruebas_ok = ejecutar_pruebas_unitarias()

    # Desde aquí se muestran los avisos de reintento, igual que en main.py.
    logging.basicConfig(level=logging.WARNING, format="   [Aviso] %(message)s",
                        stream=sys.stdout, force=True)
    temporal = tempfile.TemporaryDirectory()
    os.environ["DB_NOMBRE"] = str(Path(temporal.name) / "pruebas_en_vivo.db")
    os.environ["DB_MOTOR"] = "sqlite"
    os.environ["CLAVE_CIFRADO"] = Fernet.generate_key().decode("ascii")
    inicializar_bd()
    caida = lambda: ClienteTipoCambio(url=URL_CAIDA)  # noqa: E731

    titulo(2, "Entrada hostil en el monto: '100; DROP TABLE tipo_cambio;--'",
           "3.1.2.I.6 valida y sanea entradas antes de la consulta API")
    try:
        ServicioTipoCambio(crear_cliente=caida).consultar("100; DROP TABLE tipo_cambio;--")
        print("   ERROR: la entrada fue aceptada.")
    except ValueError as error:
        print(f"   Rechazada antes de llamar a la API: {error}")

    titulo(3, "Moneda manipulada en la ruta: '../admin'",
           "3.1.2.I.6 valida lo que viaja en la URL")
    try:
        ClienteTipoCambio().obtener_tasas("../admin")
        print("   ERROR: la moneda fue aceptada.")
    except ValueError as error:
        print(f"   Rechazada sin enviar la petición: {error}")

    titulo(4, "URL sin https", "3.1.2.I.6 credenciales y conexión segura")
    try:
        ClienteTipoCambio(url="http://open.er-api.com/v6/latest")
    except ErrorDeConfiguracion as error:
        print(f"   Rechazada: {error}")

    titulo(5, "API caída y sin datos guardados (plan C)",
           "3.1.1.I.1 timeout y reintentos 1-2-4 s con tope de 4")
    mostrar_tipo_cambio(ServicioTipoCambio(crear_cliente=caida).consultar("1"))

    titulo(6, "API real del dólar (plan A)", "3.1.1.I.1 y 3.1.1.I.2 HTTP real y JSON procesado")
    real = ServicioTipoCambio().consultar("150")
    mostrar_tipo_cambio(real)

    titulo(7, "API caída con un valor guardado (plan B)",
           "degradación controlada con obtenido_en")
    mostrar_tipo_cambio(ServicioTipoCambio(crear_cliente=caida).consultar("150"))

    titulo(8, "Supabase sin credenciales en el .env",
           "3.1.2.I.6 credenciales validadas con un mensaje claro")
    guardadas = {nombre: os.environ.get(nombre) for nombre in VARIABLES_SUPABASE}
    for nombre in VARIABLES_SUPABASE:
        os.environ[nombre] = ""
    try:
        ClienteSupabase.desde_entorno()
    except ErrorDeConfiguracion as error:
        print(f"   {error}")
    for nombre, valor in guardadas.items():
        if valor is None:
            os.environ.pop(nombre, None)
        else:
            os.environ[nombre] = valor

    titulo(9, "Supabase real: login, Bearer Token y JSON hostil",
           "3.1.2.I.5 autenticación y 3.1.1.I.2 deserialización")
    resultado = SincronizadorEmpleados().sincronizar()
    print(f"   Origen de los datos: {resultado.origen.upper()}")
    if resultado.motivo:
        print(f"   Motivo: {resultado.motivo}")
    print(f"   Guardados: {len(resultado.empleados)} · Rechazados: {len(resultado.rechazados)}")
    for empleado in resultado.empleados:
        print(f"     [guardado] {empleado.rut} {empleado.nombre}")
    for rechazo in resultado.rechazados:
        print(f"     [rechazado] {rechazo.rut}: {rechazo.motivo}")

    logging.shutdown()
    temporal.cleanup()
    print(f"\n{'=' * 72}")
    print("Pruebas automáticas: " + ("TODAS OK" if pruebas_ok else "HAY FALLAS (revisar arriba)"))
    print("Ningún escenario terminó con un traceback: el programa falla de forma controlada.")
    return 0 if pruebas_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
