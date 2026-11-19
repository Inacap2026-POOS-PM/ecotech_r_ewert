import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from infraestructura.configuracion import ErrorDeConfiguracion, requerir, requerir_url_segura
from servicios.excepciones import (ErrorAutenticacion, ErrorCertificado, ErrorPermiso,
                                   PeticionRechazada, RecursoNoEncontrado,
                                   RespuestaInvalida, ServicioNoDisponible)

log = logging.getLogger(__name__)

TIMEOUT = (3, 10)
MAXIMO_INTENTOS = 4
ESPERA_INICIAL = 1
MAXIMA_ESPERA_SERVIDOR = 60


class ClienteSupabase:
    """Consume la API REST de Supabase con Bearer Token, timeout y reintentos acotados."""

    def __init__(self, url, clave_publica, email, password, tabla="empleados", sesion=None,
                 esperar=time.sleep, azar=random.uniform, maximo_intentos=MAXIMO_INTENTOS,
                 espera_inicial=ESPERA_INICIAL, timeout=TIMEOUT):
        """Recibe la configuración ya leída; esperar y azar se reemplazan en las pruebas."""
        if not isinstance(url, str) or not url.lower().startswith("https://"):
            raise ErrorDeConfiguracion("La URL de Supabase debe comenzar con https://.")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", tabla or ""):
            raise ErrorDeConfiguracion("SUPABASE_TABLA solo admite letras, números y _.")
        if not 1 <= maximo_intentos <= 4:
            raise ValueError("El tope de intentos debe estar entre 1 y 4.")
        self._url = url.rstrip("/")
        self._clave = clave_publica
        self._email = email
        self._password = password
        self.tabla = tabla
        self._sesion = sesion or requests.Session()
        self._esperar = esperar
        self._azar = azar
        self._maximo_intentos = maximo_intentos
        self._espera_inicial = espera_inicial
        self._timeout = timeout
        self._token = None
        self._refresh_token = None

    @classmethod
    def desde_entorno(cls, **opciones):
        """Lee las credenciales del .env y falla con un mensaje claro si falta alguna."""
        url = requerir_url_segura("SUPABASE_URL")
        datos = requerir("SUPABASE_KEY", "SUPABASE_EMAIL", "SUPABASE_PASSWORD")
        tabla = os.environ.get("SUPABASE_TABLA", "").strip() or "empleados"
        return cls(url, datos["SUPABASE_KEY"], datos["SUPABASE_EMAIL"],
                   datos["SUPABASE_PASSWORD"], tabla=tabla, **opciones)

    def obtener_empleados(self):
        """Devuelve las filas JSON de la tabla de empleados."""
        if self._token is None:
            self.iniciar_sesion()
        datos = self._solicitar("GET", "/rest/v1/" + self.tabla,
                                params={"select": "*", "order": "rut.asc"}, con_token=True)
        if not isinstance(datos, list):
            raise RespuestaInvalida("Supabase no devolvió una lista de empleados.")
        return datos

    def iniciar_sesion(self):
        """Obtiene un access_token con el usuario de Supabase Auth."""
        datos = self._solicitar("POST", "/auth/v1/token", params={"grant_type": "password"},
                                cuerpo={"email": self._email, "password": self._password})
        self._guardar_tokens(datos)

    def renovar_token(self):
        """Usa el refresh_token; si también caducó, vuelve a iniciar sesión."""
        if self._refresh_token:
            try:
                datos = self._solicitar("POST", "/auth/v1/token",
                                        params={"grant_type": "refresh_token"},
                                        cuerpo={"refresh_token": self._refresh_token})
                self._guardar_tokens(datos)
                return
            except ErrorAutenticacion:
                log.warning("El refresh_token fue rechazado. Se inicia sesión otra vez.")
        self.iniciar_sesion()

    def _guardar_tokens(self, datos):
        """Conserva los tokens solo en memoria; nunca se escriben en disco ni en el log."""
        token = datos.get("access_token") if isinstance(datos, dict) else None
        if not isinstance(token, str) or not token:
            raise RespuestaInvalida("Supabase no entregó un access_token.")
        self._token = token
        refresh = datos.get("refresh_token")
        self._refresh_token = refresh if isinstance(refresh, str) and refresh else None

    def _encabezados(self, con_token):
        """La clave pública identifica el proyecto; el Bearer identifica al usuario."""
        encabezados = {"apikey": self._clave, "Accept": "application/json"}
        if con_token:
            encabezados["Authorization"] = f"Bearer {self._token}"
        return encabezados

    def _solicitar(self, metodo, ruta, params=None, cuerpo=None, con_token=False):
        """Reintenta solo lo reintentable y discrimina cada código de estado."""
        intento = 0
        token_renovado = False
        while True:
            intento += 1
            espera_servidor = None
            try:
                respuesta = self._sesion.request(metodo, self._url + ruta, params=params,
                                                 json=cuerpo, headers=self._encabezados(con_token),
                                                 timeout=self._timeout)
            except requests.exceptions.SSLError as error:
                log.error("%s %s: certificado TLS inválido.", metodo, ruta)
                raise ErrorCertificado(
                    "El certificado del servidor no es válido. No se envían credenciales.") from error
            except requests.exceptions.Timeout:
                motivo = "el servidor no respondió dentro del tiempo límite"
            except requests.exceptions.ConnectionError:
                motivo = "no hay conexión de red con el servidor"
            else:
                codigo = respuesta.status_code
                log.info("%s %s -> %s (intento %d)", metodo, ruta, codigo, intento)
                if 200 <= codigo < 300:
                    return self._leer_json(respuesta)
                if codigo == 401 and con_token and not token_renovado:
                    log.warning("401: el token expiró o fue rechazado. Se renueva una sola vez.")
                    self.renovar_token()
                    token_renovado = True
                    intento -= 1
                    continue
                if not con_token and codigo in (400, 401):
                    raise ErrorAutenticacion(
                        "Supabase rechazó el usuario o la contraseña. Revisa SUPABASE_EMAIL "
                        "y SUPABASE_PASSWORD en .env.")
                if codigo == 401:
                    raise ErrorAutenticacion("El token fue rechazado incluso después de renovarlo.")
                if codigo == 403:
                    raise ErrorPermiso("El usuario no tiene permiso para leer la tabla. "
                                       "Revisa la política RLS en Supabase.")
                if codigo == 404:
                    raise RecursoNoEncontrado(f"No existe la ruta {ruta} en Supabase.")
                if codigo == 429:
                    motivo = "se superó el límite de peticiones (429)"
                    espera_servidor = self._leer_retry_after(respuesta)
                elif codigo >= 500:
                    motivo = f"error interno del servidor ({codigo})"
                else:
                    raise PeticionRechazada(f"Supabase rechazó la petición ({codigo}).")
            if intento >= self._maximo_intentos:
                log.error("%s %s: %s. Se agotaron los %d intentos.",
                          metodo, ruta, motivo, self._maximo_intentos)
                raise ServicioNoDisponible(
                    f"Supabase no está disponible: {motivo}. "
                    f"Se agotaron los {self._maximo_intentos} intentos.")
            if espera_servidor is None:
                espera = self._espera_creciente(intento)
            else:
                espera = espera_servidor
            log.warning("Intento %d/%d falló: %s. Nuevo intento en %.1f s.",
                        intento, self._maximo_intentos, motivo, espera)
            self._esperar(espera)

    def _espera_creciente(self, intento):
        """Duplica la espera (1, 2, 4 s) y suma azar para no reintentar todos a la vez."""
        return self._espera_inicial * 2 ** (intento - 1) + self._azar(0, 0.5)

    @staticmethod
    def _leer_retry_after(respuesta):
        """Respeta Retry-After en segundos o como fecha HTTP."""
        valor = respuesta.headers.get("Retry-After", "").strip()
        if not valor:
            return None
        if valor.isdigit():
            segundos = int(valor)
        else:
            try:
                fecha = parsedate_to_datetime(valor)
            except (TypeError, ValueError):
                return None
            if fecha.tzinfo is None:
                fecha = fecha.replace(tzinfo=timezone.utc)
            segundos = max(0, (fecha - datetime.now(timezone.utc)).total_seconds())
        if segundos > MAXIMA_ESPERA_SERVIDOR:
            raise ServicioNoDisponible(
                f"Supabase pide esperar {segundos:.0f} s antes de reintentar (429).")
        return segundos

    @staticmethod
    def _leer_json(respuesta):
        """Convierte un cuerpo que no es JSON en un error controlado."""
        try:
            return respuesta.json()
        except ValueError as error:
            raise RespuestaInvalida("La respuesta de Supabase no es un JSON válido.") from error
