import logging
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests

from infraestructura.configuracion import ErrorDeConfiguracion
from servicios.excepciones import (ErrorCertificado, ErrorPermiso, PeticionRechazada,
                                   RecursoNoEncontrado, RespuestaInvalida,
                                   ServicioNoDisponible, TokenRechazado)

TIMEOUT = (3, 10)
MAXIMO_INTENTOS = 4
ESPERA_INICIAL = 1
MAXIMA_ESPERA_SERVIDOR = 60


class ClienteHTTP:
    """Base de los clientes externos: timeout, reintentos acotados y un trato por código."""

    NOMBRE = "El servicio"
    _log = logging.getLogger(__name__)

    def __init__(self, sesion=None, esperar=time.sleep, azar=random.uniform,
                 maximo_intentos=MAXIMO_INTENTOS, espera_inicial=ESPERA_INICIAL, timeout=TIMEOUT):
        """esperar y azar se reemplazan en las pruebas para no esperar segundos reales."""
        if not 1 <= maximo_intentos <= 4:
            raise ValueError("El tope de intentos debe estar entre 1 y 4.")
        self._sesion = sesion or requests.Session()
        self._esperar = esperar
        self._azar = azar
        self._maximo_intentos = maximo_intentos
        self._espera_inicial = espera_inicial
        self._timeout = timeout

    @staticmethod
    def exigir_https(url, mensaje):
        """Sin HTTPS no hay cifrado ni prueba de que el servidor sea el verdadero."""
        if not isinstance(url, str) or not url.lower().startswith("https://"):
            raise ErrorDeConfiguracion(mensaje)
        return url.rstrip("/")

    def _solicitar(self, metodo, url, params=None, cuerpo=None, encabezados=None):
        """Reintenta solo lo reintentable: red, timeout, 429 y 5xx."""
        ruta = urlsplit(url).path
        motivo = ""
        for intento in range(1, self._maximo_intentos + 1):
            espera_servidor = None
            try:
                respuesta = self._sesion.request(metodo, url, params=params, json=cuerpo,
                                                 headers=encabezados, timeout=self._timeout)
            except requests.exceptions.SSLError as error:
                # SSLError hereda de ConnectionError: se captura antes para no reintentarlo.
                self._log.error("%s %s: certificado TLS inválido.", metodo, ruta)
                raise ErrorCertificado(
                    "El certificado del servidor no es válido. No se envían datos.") from error
            except requests.exceptions.Timeout:
                motivo = "el servidor no respondió dentro del tiempo límite"
            except requests.exceptions.ConnectionError:
                motivo = "no hay conexión de red con el servidor"
            else:
                codigo = respuesta.status_code
                self._log.info("%s %s -> %s (intento %d)", metodo, ruta, codigo, intento)
                if 200 <= codigo < 300:
                    return self._leer_json(respuesta)
                if codigo == 429:
                    motivo = "se superó el límite de peticiones (429)"
                    espera_servidor = self._leer_retry_after(respuesta)
                elif codigo >= 500:
                    motivo = f"error interno del servidor ({codigo})"
                else:
                    raise self._error_del_cliente(codigo, ruta)
            if intento == self._maximo_intentos:
                break
            if espera_servidor is None:
                espera = self._espera_creciente(intento)
            else:
                espera = espera_servidor
            self._log.warning("Intento %d/%d falló: %s. Nuevo intento en %.1f s.",
                              intento, self._maximo_intentos, motivo, espera)
            self._esperar(espera)
        self._log.error("%s %s: %s. Se agotaron los %d intentos.",
                        metodo, ruta, motivo, self._maximo_intentos)
        raise ServicioNoDisponible(f"{self.NOMBRE} no está disponible: {motivo}. "
                                   f"Se agotaron los {self._maximo_intentos} intentos.")

    def _error_del_cliente(self, codigo, ruta):
        """Los 4xx son culpa de quien llama: no se reintentan."""
        if codigo == 401:
            return TokenRechazado(f"{self.NOMBRE} rechazó la autenticación (401).")
        if codigo == 403:
            return ErrorPermiso(f"{self.NOMBRE} no da permiso para esta consulta (403).")
        if codigo == 404:
            return RecursoNoEncontrado(f"No existe la ruta {ruta} en {self.NOMBRE}.")
        return PeticionRechazada(f"{self.NOMBRE} rechazó la petición ({codigo}).")

    def _espera_creciente(self, intento):
        """Duplica la espera (1, 2, 4 s) y suma azar para no reintentar todos a la vez."""
        return self._espera_inicial * 2 ** (intento - 1) + self._azar(0, 0.5)

    def _leer_retry_after(self, respuesta):
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
                f"{self.NOMBRE} pide esperar {segundos:.0f} s antes de reintentar (429).")
        return segundos

    def _leer_json(self, respuesta):
        """Convierte un cuerpo que no es JSON en un error controlado."""
        try:
            return respuesta.json()
        except ValueError as error:
            raise RespuestaInvalida(
                f"La respuesta de {self.NOMBRE} no es un JSON válido.") from error
