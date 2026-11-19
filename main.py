"""EcoTech ES3: reemplazar solo main.py en la rama basada en la entrega E2.

Conserva las carpetas originales y las dependencias de requirements.txt.
HTTP usa urllib (biblioteca estándar); Argon2id usa cryptography ya instalada.
No requiere requests, argon2-cffi ni una carpeta servicios. La persistencia es
SQLite local; esta versión no migra el sistema a Supabase.

Bitácora técnica de apoyo de IA, 19-11-2026:
Prompt: adaptar únicamente main.py del proyecto original a la guía ES3.
Acepto: consultas climáticas y monetarias con deserialización y respaldo local.
Modifico: sustituyo imports de módulos nuevos por clases dentro de este archivo,
para que la rama original pueda ejecutarlo sin cambiar otros archivos.
Modifico: Argon2id con salt aleatoria; migro hashes E2 solo tras un login correcto.
Descarto: SQL interpolado, TLS desactivado y reintentos sin límite. Uso parámetros,
verificación TLS y cuatro intentos; no registro contraseñas ni cuerpos de errores.
El estudiante debe revisar estas decisiones y explicar sus propias pruebas.
"""

import sys
import os
import tempfile
import math
import sqlite3
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from getpass import getpass
from datetime import date, datetime, timezone
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import json
import ssl
import hashlib
import hmac
import secrets
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.exceptions import InvalidKey, UnsupportedAlgorithm

from dominio.departamento import Departamento
from dominio.empleado_planta import EmpleadoPlanta
from dominio.empleado_contratista import EmpleadoContratista
from dominio.informe import Informe
from dominio.proyecto import Proyecto
from dominio.registro_tiempo import RegistroTiempo
from infraestructura.conexion import ErrorDeConexion, obtener_conexion
from infraestructura.departamento_repositorio import DepartamentoRepositorio
from infraestructura.empleado_repositorio import EmpleadoRepositorio
from infraestructura.esquema import inicializar_bd
from infraestructura.proyecto_repositorio import ProyectoRepositorio
from infraestructura.registro_tiempo_repositorio import RegistroTiempoRepositorio
from infraestructura.usuario_repositorio import UsuarioRepositorio
from seguridad.gestor_autenticacion import GestorAutenticacion as GestorOriginal


class GestorAutenticacion(GestorOriginal):
    """Conserva los roles E2, con Argon2id y compatibilidad con usuarios antiguos."""

    @staticmethod
    def generar_hash(clave):
        sal = secrets.token_bytes(16)
        kdf = Argon2id(salt=sal, length=32, iterations=3, lanes=4, memory_cost=65536)
        return sal.hex(), kdf.derive_phc_encoded(clave.encode("utf-8"))

    def registrar_usuario(self, nombre, contrasenia, rol):
        if not isinstance(nombre, str) or not 1 <= len(nombre.strip()) <= 50:
            raise ValueError("El nombre de usuario debe tener entre 1 y 50 caracteres.")
        if (not isinstance(contrasenia, str) or not contrasenia.strip()
                or not 10 <= len(contrasenia) <= 1024):
            raise ValueError("La contraseña debe tener entre 10 y 1024 caracteres y no estar en blanco.")
        if rol not in ("admin", "consulta"):
            raise ValueError("El rol debe ser admin o consulta.")
        if self.repositorio.hay_usuarios():
            if not self.verificar_rol(self.usuario_actual, "usuarios"):
                raise ValueError("Solo un administrador puede crear usuarios.")
        elif rol != "admin":
            raise ValueError("El primer usuario debe ser administrador.")
        sal, hash_clave = self.generar_hash(contrasenia)
        self.repositorio.guardar(nombre.strip(), sal, hash_clave, rol)

    def iniciar_sesion(self, usuario, contrasenia):
        self.usuario_actual = None
        if (not isinstance(usuario, str) or not usuario.strip()
                or not isinstance(contrasenia, str) or not contrasenia.strip()
                or len(contrasenia) > 1024):
            return False
        datos = self.repositorio.obtener(usuario.strip())
        if datos is None:
            return False
        try:
            if datos[2].startswith("$argon2id$"):
                Argon2id.verify_phc_encoded(contrasenia.encode("utf-8"), datos[2])
            else:
                sal = bytes.fromhex(datos[1])
                calculado = hashlib.pbkdf2_hmac("sha256", contrasenia.encode("utf-8"), sal, 600000)
                if not hmac.compare_digest(calculado.hex(), datos[2]):
                    return False
                nueva_sal, nuevo_hash = self.generar_hash(contrasenia)
                with obtener_conexion() as conexion:
                    conexion.execute("UPDATE usuario SET sal = ?, hash_clave = ? WHERE nombre = ?",
                                     (nueva_sal, nuevo_hash, datos[0]))
        except (ValueError, InvalidKey):
            return False
        self.usuario_actual = datos[0]
        return True


class SinRedirecciones(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Evita redirecciones a HTTP y peticiones extra fuera del límite.
        return None


def espera_retry_after(valor, respaldo):
    if not valor:
        return respaldo
    try:
        if valor.strip().isdigit():
            return int(valor.strip())
        fecha = parsedate_to_datetime(valor)
        if fecha.utcoffset() is None:
            return respaldo
        return max(0, (fecha - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return respaldo


def obtener_json(url):
    """GET HTTPS con timeout, cuatro intentos y sin exponer errores de la red.

    Solo se llama con URLs públicas fijas construidas por este programa.
    Estas APIs no necesitan tokens; 401/403 no se reintentan.
    """
    if not url.startswith(("https://mindicador.cl/api/", "https://api.open-meteo.com/v1/forecast?")):
        raise ValueError("Servicio externo no permitido.")
    cliente = build_opener(SinRedirecciones(), HTTPSHandler(context=ssl.create_default_context()))
    peticion = Request(url, headers={"Accept": "application/json", "User-Agent": "EcoTech-ES3/1.0"})
    for intento in range(1, 5):
        espera = 2 ** (intento - 1)
        logging.info("API intento=%s/4", intento)
        try:
            with cliente.open(peticion, timeout=10) as respuesta:
                logging.info("API HTTP=%s", respuesta.status)
                if respuesta.status != 200:
                    raise ValueError("El servicio no entregó una respuesta HTTP 200.")
                contenido = respuesta.read(2_000_001)
                if len(contenido) > 2_000_000:
                    raise ValueError("La respuesta del servicio supera el tamaño permitido.")
                try:
                    return json.loads(contenido.decode("utf-8"))
                except (ValueError, UnicodeError, RecursionError) as error:
                    raise ValueError("La API no entregó JSON válido.") from error
        except HTTPError as error:
            try:
                logging.warning("API HTTP=%s", error.code)
                if error.code == 429:
                    espera = espera_retry_after(error.headers.get("Retry-After"), espera)
                elif not 500 <= error.code <= 599:
                    raise ValueError(f"Respuesta HTTP {error.code}; no se reintenta.") from error
            finally:
                error.close()
        except ssl.SSLError as error:
            raise ValueError("No se pudo verificar la conexión HTTPS.") from error
        except URLError as error:
            if isinstance(error.reason, ssl.SSLError):
                raise ValueError("No se pudo verificar la conexión HTTPS.") from error
            logging.warning("Fallo de conexión con la API.")
        except (OSError, HTTPException):
            logging.warning("Fallo de red o tiempo de espera agotado.")
        if intento < 4:
            if espera > 60:
                raise ValueError("La API exige una espera larga; se consultará el respaldo sin reintentar.")
            logging.info("Reintento en %.1f segundos", espera)
            time.sleep(espera)
    raise ValueError("No se pudo consultar la API después de cuatro intentos.")


@dataclass(frozen=True)
class Indicador:
    codigo: str
    nombre: str
    unidad: str
    valor: float
    fecha: datetime
    obtenido_en: datetime

    def __post_init__(self):
        if self.codigo not in ("uf", "dolar") or self.unidad != "Pesos":
            raise ValueError("Indicador o unidad no permitido.")
        if not isinstance(self.nombre, str) or not self.nombre.strip() or len(self.nombre) > 100:
            raise ValueError("Nombre de indicador inválido.")
        if type(self.valor) not in (int, float) or not math.isfinite(self.valor) or self.valor <= 0:
            raise ValueError("El valor del indicador debe ser un número positivo y finito.")
        for fecha in (self.fecha, self.obtenido_en):
            if not isinstance(fecha, datetime) or fecha.utcoffset() is None:
                raise ValueError("El indicador contiene una fecha inválida.")


class DeserializadorIndicador:
    @staticmethod
    def convertir(datos, codigo):
        try:
            if datos["codigo"] != codigo or not isinstance(datos["serie"], list) or not datos["serie"]:
                raise ValueError("Serie de indicadores inválida.")
            obtenido = datetime.now(timezone.utc)
            indicadores = [Indicador(codigo, datos["nombre"], datos["unidad_medida"], fila["valor"],
                                     datetime.fromisoformat(fila["fecha"].replace("Z", "+00:00")), obtenido)
                           for fila in datos["serie"]]
            return max(indicadores, key=lambda dato: dato.fecha)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as error:
            raise ValueError("La API entregó un indicador incompleto o inválido.") from error


@dataclass(frozen=True)
class ResultadoIndicador:
    indicador: Indicador | None
    desde_cache: bool
    aviso: str


class ServicioIndicadores:
    """Consume y guarda indicadores sin depender de módulos nuevos de la entrega."""

    @staticmethod
    def respaldo(codigo, dato=None):
        with obtener_conexion() as conexion:
            conexion.execute("""CREATE TABLE IF NOT EXISTS indicador_cache (
                codigo TEXT PRIMARY KEY, nombre TEXT NOT NULL, unidad TEXT NOT NULL,
                valor REAL NOT NULL, fecha TEXT NOT NULL, obtenido_en TEXT NOT NULL)""")
            if dato is not None:
                conexion.execute("""INSERT INTO indicador_cache
                    (codigo, nombre, unidad, valor, fecha, obtenido_en) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(codigo) DO UPDATE SET nombre=excluded.nombre, unidad=excluded.unidad,
                    valor=excluded.valor, fecha=excluded.fecha, obtenido_en=excluded.obtenido_en""",
                    (dato.codigo, dato.nombre, dato.unidad, dato.valor,
                     dato.fecha.isoformat(), dato.obtenido_en.isoformat()))
                return dato
            fila = conexion.execute("SELECT codigo, nombre, unidad, valor, fecha, obtenido_en "
                                    "FROM indicador_cache WHERE codigo = ?", (codigo,)).fetchone()
        if fila is None:
            return None
        return Indicador(*fila[:4], datetime.fromisoformat(fila[4]), datetime.fromisoformat(fila[5]))

    def consultar(self, codigo):
        if codigo not in ("uf", "dolar"):
            raise ValueError("Elige uf o dolar.")
        try:
            datos = obtener_json("https://mindicador.cl/api/" + codigo)
            dato = DeserializadorIndicador.convertir(datos, codigo)
        except ValueError as error:
            logging.warning("Se consultará el respaldo del indicador.")
            try:
                dato = self.respaldo(codigo)
            except (ErrorDeConexion, sqlite3.Error, ValueError, TypeError):
                return ResultadoIndicador(None, False, "Falló la API y no se pudo leer el respaldo local.")
            mensaje = " Se muestra la copia local." if dato else " Todavía no hay datos locales."
            return ResultadoIndicador(dato, dato is not None, str(error) + mensaje)
        try:
            self.respaldo(codigo, dato)
        except (ErrorDeConexion, sqlite3.Error):
            return ResultadoIndicador(dato, False, "Dato recibido, pero no se pudo guardar el respaldo local.")
        return ResultadoIndicador(dato, False, "Dato recibido de la API y guardado localmente.")


# Lugares permitidos: evita ciudades ambiguas y consultas con datos arbitrarios.
CIUDADES = {
    "1": ("Santiago, Chile", -33.45, -70.67),
    "2": ("Concepción, Chile", -36.83, -73.05),
    "3": ("Puerto Montt, Chile", -41.47, -72.94),
    "4": ("Nueva York, Estados Unidos", 40.71, -74.01),
}
ESTADOS_CLIMA = {
    0: "Despejado", 1: "Mayormente despejado", 2: "Parcialmente nublado",
    3: "Nublado", 45: "Niebla", 48: "Niebla con escarcha",
    51: "Llovizna leve", 53: "Llovizna moderada", 55: "Llovizna intensa",
    56: "Llovizna helada leve", 57: "Llovizna helada intensa",
    61: "Lluvia leve", 63: "Lluvia moderada", 65: "Lluvia intensa",
    66: "Lluvia helada leve", 67: "Lluvia helada intensa",
    71: "Nevada leve", 73: "Nevada moderada", 75: "Nevada intensa",
    77: "Granos de nieve", 80: "Chubascos leves", 81: "Chubascos moderados",
    82: "Chubascos intensos", 85: "Chubascos de nieve leves",
    86: "Chubascos de nieve intensos", 95: "Tormenta",
    96: "Tormenta con granizo leve", 99: "Tormenta con granizo intenso",
}


@dataclass(frozen=True)
class Clima:
    ciudad: str
    temperatura: float
    humedad: float
    codigo: int
    fecha: datetime
    obtenido_en: datetime

    def __post_init__(self):
        if self.ciudad not in {datos[0] for datos in CIUDADES.values()}:
            raise ValueError("Ciudad no permitida.")
        for valor, minimo, maximo in ((self.temperatura, -100, 70), (self.humedad, 0, 100)):
            if (type(valor) not in (int, float) or not math.isfinite(valor)
                    or not minimo <= valor <= maximo):
                raise ValueError("El clima contiene valores fuera de rango.")
        if type(self.codigo) is not int or self.codigo not in ESTADOS_CLIMA:
            raise ValueError("Estado climático desconocido.")
        for fecha in (self.fecha, self.obtenido_en):
            if not isinstance(fecha, datetime) or fecha.utcoffset() is None:
                raise ValueError("El clima contiene fechas inválidas.")


class DeserializadorClima:
    """Convierte el JSON de Open-Meteo a un objeto de dominio validado."""

    @staticmethod
    def convertir(datos, ciudad):
        try:
            actual = datos["current"]
            unidades = datos["current_units"]
            if unidades["temperature_2m"] != "°C" or unidades["relative_humidity_2m"] != "%":
                raise ValueError("Unidades climáticas incorrectas.")
            instante = actual["time"]
            if type(instante) not in (int, float) or not math.isfinite(instante):
                raise ValueError("Fecha climática incorrecta.")
            return Clima(ciudad, actual["temperature_2m"], actual["relative_humidity_2m"],
                         actual["weather_code"], datetime.fromtimestamp(instante, timezone.utc),
                         datetime.now(timezone.utc))
        except (KeyError, TypeError, ValueError, OverflowError, OSError) as error:
            raise ValueError("La API entregó datos climáticos incompletos o inválidos.") from error


def obtener_clima(opcion):
    """API pública de Open-Meteo: https://open-meteo.com/en/docs"""
    if opcion not in CIUDADES:
        raise ValueError("Selecciona una ciudad de la lista.")
    ciudad, latitud, longitud = CIUDADES[opcion]
    parametros = {"latitude": latitud, "longitude": longitud,
                  "current": "temperature_2m,relative_humidity_2m,weather_code",
                  "temperature_unit": "celsius", "timeformat": "unixtime", "timezone": "GMT"}
    datos = obtener_json("https://api.open-meteo.com/v1/forecast?" + urlencode(parametros))
    return DeserializadorClima.convertir(datos, ciudad)


def respaldo_clima(ciudad, dato=None):
    """Crea su tabla al usarla; no exige editar el esquema del proyecto."""
    with obtener_conexion() as conexion:
        conexion.execute("""CREATE TABLE IF NOT EXISTS clima_cache (
            ciudad TEXT PRIMARY KEY, temperatura REAL NOT NULL, humedad REAL NOT NULL,
            codigo INTEGER NOT NULL, fecha TEXT NOT NULL, obtenido_en TEXT NOT NULL)""")
        if dato is not None:
            conexion.execute("""INSERT INTO clima_cache VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ciudad) DO UPDATE SET temperatura=excluded.temperatura,
                humedad=excluded.humedad, codigo=excluded.codigo,
                fecha=excluded.fecha, obtenido_en=excluded.obtenido_en""",
                (dato.ciudad, dato.temperatura, dato.humedad, dato.codigo,
                 dato.fecha.isoformat(), dato.obtenido_en.isoformat()))
            return dato
        fila = conexion.execute("SELECT ciudad, temperatura, humedad, codigo, fecha, obtenido_en "
                                "FROM clima_cache WHERE ciudad = ?", (ciudad,)).fetchone()
    if fila is None:
        return None
    return Clima(*fila[:4], datetime.fromisoformat(fila[4]), datetime.fromisoformat(fila[5]))


def mostrar_antiguedad(fecha):
    minutos = max(0, (datetime.now(timezone.utc) - fecha).total_seconds() / 60)
    print(f"Obtenido en: {fecha.isoformat()} | Antigüedad: {minutos:.1f} minutos")


def consultar_clima():
    for clave, datos in CIUDADES.items():
        print(f"{clave} {datos[0]}")
    opcion = input("Ciudad donde se realizará el proyecto: ").strip()
    if opcion not in CIUDADES:
        raise ValueError("Selecciona una ciudad de la lista.")
    try:
        dato = obtener_clima(opcion)
    except ValueError as error:
        print(error)
        try:
            dato = respaldo_clima(CIUDADES[opcion][0])
        except (ErrorDeConexion, sqlite3.Error, ValueError, TypeError):
            print("No se pudo leer el respaldo climático local.")
            return
        if dato is None:
            print("Todavía no hay clima guardado para esta ciudad. Volviendo al menú.")
            return
        print("RESPALDO LOCAL: estos datos no se actualizaron en esta consulta.")
    else:
        try:
            respaldo_clima(dato.ciudad, dato)
            print("Clima recibido de la API y guardado localmente.")
        except (ErrorDeConexion, sqlite3.Error):
            print("Clima recibido, pero no se pudo guardar el respaldo local.")
    print(f"{dato.ciudad}: {dato.temperatura:g} °C | Humedad: {dato.humedad:g}%")
    print(f"Estado: {ESTADOS_CLIMA[dato.codigo]} | Fecha del dato: {dato.fecha.isoformat()}")
    mostrar_antiguedad(dato.obtenido_en)
    print("Fuente: Open-Meteo. Condiciones actuales; no es un pronóstico para fechas futuras.")
    if dato.codigo >= 45:
        print("Planificación: revisar las condiciones antes de realizar trabajos al aire libre.")


def calcular_pago_internacional():
    """Estimación CLP a USD; no registra ni ejecuta un pago real."""
    moneda = input("Moneda de destino disponible (USD): ").strip().upper()
    if moneda != "USD":
        raise ValueError("Por ahora se admite USD (dólar estadounidense).")
    empleado = pedir_empleado()
    resultado = ServicioIndicadores().consultar("dolar")
    print(resultado.aviso)
    dato = resultado.indicador
    if dato is None:
        return
    pago = Decimal(str(empleado.calcular_pago()))
    tasa = Decimal(str(dato.valor))
    if not pago.is_finite() or pago < 0 or not tasa.is_finite() or tasa <= 0:
        raise ValueError("El pago o el tipo de cambio es inválido.")
    monto = (pago / tasa).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    print(f"Pago bruto acumulado: {pago:,.0f} CLP | Referencia: 1 USD = {tasa:,.2f} CLP")
    print(f"Equivalente estimado: {monto:,.2f} USD | Fecha del dólar: {dato.fecha.isoformat()}")
    mostrar_antiguedad(dato.obtenido_en)
    if resultado.desde_cache:
        print("RESPALDO LOCAL: estimación con una cotización que no se actualizó.")
    print("No incluye comisiones ni descuentos; no se ejecutó ningún pago.")


def validar_rut_nuevo(valor):
    """Comprueba módulo 11 al crear; permite consultar registros antiguos de E2."""
    from dominio.validador_entradas import ValidadorEntradas
    rut = ValidadorEntradas.rut(valor.strip().replace(".", "").upper())
    cuerpo, digito = rut.split("-")
    suma = sum(int(numero) * (2 + indice % 6) for indice, numero in enumerate(reversed(cuerpo)))
    resultado = 11 - suma % 11
    esperado = "0" if resultado == 11 else "K" if resultado == 10 else str(resultado)
    if int(cuerpo) == 0 or digito != esperado:
        raise ValueError("El dígito verificador del RUT es incorrecto.")
    return rut



def mostrar_empleado(empleado):
    """Muestra el contrato y los totales calculados a partir de sus jornadas."""
    departamento = empleado.departamento.nombre if empleado.departamento else "Sin asignar"
    print(f"{empleado.rut} | {empleado.nombre} | {type(empleado).__name__}")
    print(f"Departamento: {departamento} | Horas: {empleado.total_horas():g} | "
          f"Pago bruto: ${empleado.calcular_pago():,.0f}")


def pedir_empleado():
    """Recupera el empleado que se quiere gestionar."""
    empleado = EmpleadoRepositorio().obtener(input("RUT: ").strip().upper())
    if empleado is None:
        raise ValueError("No existe un empleado con ese RUT.")
    return empleado


def crear_empleado():
    """Pide datos, valida mediante las clases y guarda el contrato elegido."""
    tipo = input("Contrato (1 planta / 2 contratista): ").strip()
    if tipo not in ("1", "2"):
        raise ValueError("El contrato debe ser 1 o 2.")
    rut = validar_rut_nuevo(input("RUT con dígito verificador: "))
    nombre = input("Nombre: ")
    fecha = date.fromisoformat(input("Ingreso (AAAA-MM-DD): ").strip())
    sueldo = float(input("Sueldo base de referencia: "))
    direccion = input("Dirección (opcional): ")
    correo = input("Correo (opcional): ")
    telefono = input("Teléfono (opcional): ")
    if tipo == "1":
        bono = float(input("Bono: "))
        empleado = EmpleadoPlanta(rut, nombre, fecha, sueldo, bono,
                                  direccion, telefono, correo)
    else:
        tarifa = float(input("Tarifa por hora: "))
        empleado = EmpleadoContratista(rut, nombre, fecha, sueldo, tarifa,
                                       direccion, telefono, correo)
    departamento = DepartamentoRepositorio().obtener(int(input("ID del departamento: ")))
    if departamento is None:
        raise ValueError("Primero debes crear el departamento.")
    departamento.asignar_empleado(empleado)
    EmpleadoRepositorio().guardar(empleado)
    print("Empleado guardado.")


def listar_empleados():
    """Busca empleados y muestra sus totales reales."""
    empleados = EmpleadoRepositorio().listar(input("Filtrar nombre (Enter para todos): "))
    for empleado in empleados:
        mostrar_empleado(empleado)
    if not empleados:
        print("No hay empleados para ese filtro.")


def actualizar_empleado():
    """Permite cambiar los datos principales sin editar directamente la base."""
    empleado = pedir_empleado()
    empleado.nombre = input(f"Nombre [{empleado.nombre}]: ").strip() or empleado.nombre
    sueldo = input(f"Sueldo base [{empleado.sueldo_base}]: ").strip()
    if sueldo:
        empleado.sueldo_base = float(sueldo)
    if isinstance(empleado, EmpleadoPlanta):
        valor = input(f"Bono [{empleado.bono_sostenibilidad}]: ").strip()
        if valor:
            empleado.bono_sostenibilidad = float(valor)
    else:
        valor = input(f"Tarifa [{empleado.tarifa_hora}]: ").strip()
        if valor:
            empleado.tarifa_hora = float(valor)
    EmpleadoRepositorio().actualizar(empleado)
    print("Empleado actualizado.")


def eliminar_empleado():
    """Solicita confirmación antes de borrar un empleado y sus jornadas."""
    empleado = pedir_empleado()
    if input(f"¿Eliminar a {empleado.nombre} y sus jornadas? (s/n): ").strip().lower() == "s":
        EmpleadoRepositorio().eliminar(empleado.rut)
        print("Empleado eliminado.")


def gestionar_departamentos():
    """Ofrece el CRUD del segundo repositorio exigido en el laboratorio."""
    repo = DepartamentoRepositorio()
    for dep in repo.listar():
        print(f"{dep.id} | {dep.nombre} | Responsable: {dep.gerente}")
    opcion = input("1 Crear / 2 Modificar / 3 Eliminar / Enter Volver: ").strip()
    if not opcion:
        return
    if opcion == "1":
        dep = repo.guardar(Departamento(input("Nombre: "), gerente=input("Responsable: ")))
        print(f"Departamento guardado con ID {dep.id}.")
    elif opcion in ("2", "3"):
        dep = repo.obtener(int(input("ID: ")))
        if dep is None:
            raise ValueError("No existe el departamento.")
        if opcion == "2":
            dep.nombre = input("Nuevo nombre: ")
            dep.gerente = input("Responsable: ")
            repo.actualizar(dep)
            print("Departamento actualizado.")
        elif input("¿Confirmas eliminarlo? (s/n): ").strip().lower() == "s":
            repo.eliminar(dep.id)
            print("Departamento eliminado.")
    else:
        raise ValueError("Opción inválida.")


def gestionar_proyectos():
    """Crea, consulta, modifica y elimina los datos propios de los proyectos."""
    repo = ProyectoRepositorio()
    for proyecto in repo.listar():
        print(f"{proyecto.id} | {proyecto.nombre} | {proyecto.fecha_inicio}")
    opcion = input("1 Crear / 2 Modificar / 3 Eliminar / Enter Volver: ").strip()
    if not opcion:
        return
    if opcion == "1":
        proyecto = Proyecto(None, input("Nombre: "),
            date.fromisoformat(input("Inicio (AAAA-MM-DD): ").strip()), input("Descripción: "))
        repo.guardar(proyecto)
        print(f"Proyecto guardado con ID {proyecto.id}.")
    elif opcion in ("2", "3"):
        proyecto = repo.obtener(int(input("ID: ")))
        if proyecto is None:
            raise ValueError("No existe el proyecto.")
        if opcion == "2":
            proyecto.nombre = input("Nuevo nombre: ")
            proyecto.descripcion = input("Descripción: ")
            repo.actualizar(proyecto)
            print("Proyecto actualizado.")
        elif input("¿Confirmas eliminarlo? (s/n): ").strip().lower() == "s":
            repo.eliminar(proyecto.id)
            print("Proyecto eliminado.")
    else:
        raise ValueError("Opción inválida.")


def asignar_proyecto():
    """Asigna un empleado ya guardado a un proyecto existente."""
    empleado = pedir_empleado()
    proyecto = ProyectoRepositorio().obtener(int(input("ID del proyecto: ")))
    if proyecto is None:
        raise ValueError("No existe el proyecto.")
    nuevo = EmpleadoRepositorio().asignar_proyecto(empleado, proyecto)
    print("Asignación guardada." if nuevo else "El empleado ya estaba asignado.")


def registrar_horas():
    """Guarda una jornada de un empleado en uno de sus proyectos asignados."""
    empleado = pedir_empleado()
    for proyecto in empleado.proyectos:
        print(f"{proyecto.id} | {proyecto.nombre}")
    identificador = int(input("ID del proyecto asignado: "))
    proyecto = None
    for asignado in empleado.proyectos:
        if asignado.id == identificador:
            proyecto = asignado
            break
    if proyecto is None:
        raise ValueError("El empleado no está asignado a ese proyecto.")
    registro = RegistroTiempo(date.fromisoformat(input("Fecha (AAAA-MM-DD): ").strip()),
        float(input("Horas: ")), input("Actividad: "), empleado, proyecto)
    registro.ingresar_registro()
    RegistroTiempoRepositorio().guardar(registro)
    print("Jornada guardada.")


def cambiar_departamento():
    """Reasigna al empleado a una única área."""
    empleado = pedir_empleado()
    departamento = DepartamentoRepositorio().obtener(int(input("ID del departamento: ")))
    if departamento is None:
        raise ValueError("No existe el departamento.")
    departamento.asignar_empleado(empleado)
    EmpleadoRepositorio().actualizar(empleado)
    print("Departamento actualizado.")


def iniciar_acceso():
    """Prepara el primer administrador y permite tres intentos de acceso."""
    gestor = GestorAutenticacion(UsuarioRepositorio())
    if not gestor.repositorio.hay_usuarios():
        print("Primera ejecución: crea tu administrador.")
        nombre = input("Nuevo usuario: ")
        clave = getpass("Contraseña (mínimo 10 caracteres): ")
        if not clave.strip():
            raise ValueError("La contraseña no puede estar vacía ni contener solo espacios.")
        if clave != getpass("Repite la contraseña: "):
            raise ValueError("Las contraseñas no coinciden.")
        gestor.registrar_usuario(nombre, clave, "admin")
        print("Administrador creado.")
    for intento in range(3):
        usuario = input("Usuario: ").strip()
        clave = getpass("Contraseña: ")
        if not usuario or not clave.strip():
            print("El usuario y la contraseña no pueden estar vacíos.")
            continue
        if gestor.iniciar_sesion(usuario, clave):
            return gestor
        print("Usuario o contraseña incorrectos.")
    raise ValueError("Se agotaron los intentos. Vuelve a iniciar el programa.")


def crear_usuario(gestor):
    """El administrador puede dar acceso de consulta a sus compañeros."""
    nombre = input("Nuevo usuario: ")
    rol = input("Rol (admin / consulta): ").strip().lower()
    clave = getpass("Contraseña (mínimo 10 caracteres): ")
    if not clave.strip():
        raise ValueError("La contraseña no puede estar vacía ni contener solo espacios.")
    if clave != getpass("Repite la contraseña: "):
        raise ValueError("Las contraseñas no coinciden.")
    gestor.registrar_usuario(nombre, clave, rol)
    print("Usuario guardado.")


def consultar_indicador():
    codigo = input("Indicador (uf / dolar): ").strip().lower()
    resultado = ServicioIndicadores().consultar(codigo)
    print(resultado.aviso)
    dato = resultado.indicador
    if dato is not None:
        minutos = max(0, (datetime.now(timezone.utc) - dato.obtenido_en).total_seconds() / 60)
        print(f"{dato.nombre}: ${dato.valor:,.2f} {dato.unidad}")
        print(f"Fecha del valor: {dato.fecha.isoformat()}")
        print(f"Obtenido en: {dato.obtenido_en.isoformat()} | Antigüedad: {minutos:.1f} minutos")
        if resultado.desde_cache:
            print("RESPALDO LOCAL: este valor no se ha actualizado en esta consulta.")


def menu(gestor):
    """Mantiene el menú disponible ante entradas inválidas o errores esperados."""
    if not gestor.verificar_rol(gestor.usuario_actual, "consulta"):
        print("Debes iniciar sesión antes de acceder al menú.")
        return
    while True:
        print("\nEcoTech\n1 Crear empleado\n2 Consultar empleados\n3 Actualizar empleado"
              "\n4 Eliminar empleado\n5 Departamentos\n6 Proyectos\n7 Asignar proyecto"
              "\n8 Registrar horas\n9 Cambiar departamento\n10 Crear usuario"
              "\n11 Consultar UF o dólar\n12 Consultar clima para proyectos"
              "\n13 Estimar pago en dólares\n0 Salir")
        opcion = input("Opción: ").strip()
        if opcion == "0":
            return
        if opcion not in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13"):
            print("Elige una opción de la lista.")
            continue
        modulo = "consulta" if opcion in ("2", "11", "12") else "administrar"
        if not gestor.verificar_rol(gestor.usuario_actual, modulo):
            print("Tu usuario no tiene permiso para esa opción.")
            continue
        try:
            if opcion == "1":
                crear_empleado()
            elif opcion == "2":
                listar_empleados()
            elif opcion == "3":
                actualizar_empleado()
            elif opcion == "4":
                eliminar_empleado()
            elif opcion == "5":
                gestionar_departamentos()
            elif opcion == "6":
                gestionar_proyectos()
            elif opcion == "7":
                asignar_proyecto()
            elif opcion == "8":
                registrar_horas()
            elif opcion == "9":
                cambiar_departamento()
            elif opcion == "10":
                crear_usuario(gestor)
            elif opcion == "11":
                consultar_indicador()
            elif opcion == "12":
                consultar_clima()
            elif opcion == "13":
                calcular_pago_internacional()
            else:
                print("Elige una opción de la lista.")
        except ValueError as error:
            print(f"No se pudo completar la operación: {error}")
        except (ErrorDeConexion, sqlite3.Error, OSError, InvalidOperation, UnsupportedAlgorithm):
            logging.warning("Operación no completada por un fallo de almacenamiento o cálculo.")
            print("No se pudo completar la operación. Revisa la configuración o los datos ingresados.")


def _demostrar():
    """Ejecuta un ciclo comprobable usando todos los objetos centrales."""
    inicializar_bd()
    departamentos = DepartamentoRepositorio()
    empleados = EmpleadoRepositorio()
    proyectos = ProyectoRepositorio()
    departamento = departamentos.guardar(Departamento("Tecnología", gerente="Ana"))
    proyecto = proyectos.guardar(Proyecto(None, "SolarEco", date(2026, 1, 1), "Energía solar"))
    ana = EmpleadoPlanta("12345678-9", "Ana", date(2024, 1, 1), 950000, 50000)
    luis = EmpleadoContratista("11111111-1", "Luis", date(2024, 1, 1), 950000, 10000)
    for empleado in (ana, luis):
        departamento.asignar_empleado(empleado)
        empleados.guardar(empleado)
        empleados.asignar_proyecto(empleado, proyecto)
        registro = RegistroTiempo(date(2026, 1, 2), 3, "Desarrollo", empleado, proyecto)
        registro.ingresar_registro()
        RegistroTiempoRepositorio().guardar(registro)
        mostrar_empleado(empleados.obtener(empleado.rut))
    ana.sueldo_base = 1000000
    empleados.actualizar(ana)
    departamento.nombre = "Desarrollo sostenible"
    departamentos.actualizar(departamento)
    proyecto.descripcion = "Prueba del CRUD de proyectos"
    proyectos.actualizar(proyecto)
    print(Informe("Resumen", date(2026, 1, 2), proyecto).generar())
    for repositorio in (empleados, departamentos, proyectos):
        resultado = repositorio.listar("' OR '1'='1")
        if resultado:
            raise RuntimeError("La prueba de inyección devolvió datos inesperados.")
    print("Consultas parametrizadas: prueba superada.")
    for empleado in (ana, luis):
        empleados.eliminar(empleado.rut)
    proyectos.eliminar(proyecto.id)
    departamentos.eliminar(departamento.id)
    print("CRUD, contratos y registros: demostración terminada.")


def ejecutar_laboratorio():
    """La demostración usa una base temporal y no toca los datos de trabajo."""
    anteriores = {}
    for clave in ("DB_NOMBRE", "DB_MOTOR"):
        anteriores[clave] = os.environ.get(clave)
    try:
        with tempfile.TemporaryDirectory() as carpeta:
            os.environ["DB_NOMBRE"] = str(Path(carpeta) / "demo.db")
            os.environ["DB_MOTOR"] = "sqlite"
            _demostrar()
    finally:
        for clave, valor in anteriores.items():
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor


def main():
    """Inicia el menú o una demostración independiente."""
    manejadores = [logging.StreamHandler()]
    try:
        manejadores.append(RotatingFileHandler(Path(__file__).parent / "ecotech.log",
            maxBytes=500000, backupCount=2, encoding="utf-8"))
    except OSError:
        print("No se pudo abrir el archivo de log; se registrará en consola.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=manejadores)
    try:
        if len(sys.argv) == 2 and sys.argv[1] == "--demo":
            ejecutar_laboratorio()
        elif len(sys.argv) == 1:
            inicializar_bd()
            gestor = iniciar_acceso()
            menu(gestor)
        else:
            print("Usa python main.py o python main.py --demo.")
            return 1
    except ValueError as error:
        print(f"No se pudo iniciar EcoTech: {error}")
        return 1
    except (ErrorDeConexion, sqlite3.Error, OSError, UnsupportedAlgorithm):
        print("No se pudo acceder a los archivos de EcoTech. Revisa la configuración y los permisos.")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nSesión finalizada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
