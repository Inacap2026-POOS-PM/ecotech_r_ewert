import logging
import os
import re

from dominio.validador_entradas import ValidadorEntradas
from infraestructura.configuracion import ErrorDeConfiguracion, requerir, requerir_url_segura
from servicios.cliente_http import ClienteHTTP
from servicios.excepciones import (ErrorAutenticacion, ErrorPermiso, RespuestaInvalida,
                                   TokenRechazado)

RUTA_AUTENTICACION = "/auth/v1/token"


class ClienteSupabase(ClienteHTTP):
    """Consume la API REST de Supabase con Bearer Token y renovación ante un 401."""

    NOMBRE = "Supabase"
    _log = logging.getLogger(__name__)

    def __init__(self, url, clave_publica, email, password, tabla="empleados", **opciones):
        """Recibe la configuración ya leída; las opciones de red van a ClienteHTTP."""
        self._url = self.exigir_https(url, "La URL de Supabase debe comenzar con https://.")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", tabla or ""):
            raise ErrorDeConfiguracion("SUPABASE_TABLA solo admite letras, números y _.")
        super().__init__(**opciones)
        self._clave = clave_publica
        self._email = email
        self._password = password
        self.tabla = tabla
        self._token = None
        self._refresh_token = None

    @classmethod
    def desde_entorno(cls, **opciones):
        """Lee y valida las credenciales del .env; falla con un mensaje claro si algo falta."""
        datos = requerir("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_EMAIL", "SUPABASE_PASSWORD")
        url = requerir_url_segura("SUPABASE_URL")
        if datos["SUPABASE_KEY"].startswith("sb_secret_"):
            raise ErrorDeConfiguracion("SUPABASE_KEY es la clave secreta. "
                                       "Usa la clave anon o publishable.")
        if not ValidadorEntradas.validar_email(datos["SUPABASE_EMAIL"]):
            raise ErrorDeConfiguracion("SUPABASE_EMAIL no tiene formato de correo.")
        tabla = os.environ.get("SUPABASE_TABLA", "").strip() or "empleados"
        return cls(url, datos["SUPABASE_KEY"], datos["SUPABASE_EMAIL"],
                   datos["SUPABASE_PASSWORD"], tabla=tabla, **opciones)

    def obtener_empleados(self):
        """Devuelve las filas JSON; si el token venció (401), lo renueva una sola vez."""
        if self._token is None:
            self.iniciar_sesion()
        try:
            datos = self._pedir_tabla()
        except TokenRechazado:
            self._log.warning("401: el token expiró o fue rechazado. Se renueva una sola vez.")
            self.renovar_token()
            try:
                datos = self._pedir_tabla()
            except TokenRechazado as error:
                raise ErrorAutenticacion(
                    "El token fue rechazado incluso después de renovarlo.") from error
        if not isinstance(datos, list):
            raise RespuestaInvalida("Supabase no devolvió una lista de empleados.")
        return datos

    def _pedir_tabla(self):
        """GET a la tabla con el Bearer Token vigente."""
        return self._solicitar("GET", self._url + "/rest/v1/" + self.tabla,
                               params={"select": "*", "order": "rut.asc"},
                               encabezados=self._encabezados(con_token=True))

    def iniciar_sesion(self):
        """Obtiene un access_token con el usuario de Supabase Auth."""
        datos = self._solicitar("POST", self._url + RUTA_AUTENTICACION,
                                params={"grant_type": "password"},
                                cuerpo={"email": self._email, "password": self._password},
                                encabezados=self._encabezados(con_token=False))
        self._guardar_tokens(datos)

    def renovar_token(self):
        """Usa el refresh_token; si también caducó, vuelve a iniciar sesión."""
        if self._refresh_token:
            try:
                datos = self._solicitar("POST", self._url + RUTA_AUTENTICACION,
                                        params={"grant_type": "refresh_token"},
                                        cuerpo={"refresh_token": self._refresh_token},
                                        encabezados=self._encabezados(con_token=False))
                self._guardar_tokens(datos)
                return
            except ErrorAutenticacion:
                self._log.warning("El refresh_token fue rechazado. Se inicia sesión otra vez.")
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

    def _error_del_cliente(self, codigo, ruta):
        """Mensajes propios de Supabase para el inicio de sesión y la política RLS."""
        if ruta == RUTA_AUTENTICACION and codigo in (400, 401):
            return ErrorAutenticacion("Supabase rechazó el usuario o la contraseña. Revisa "
                                      "SUPABASE_EMAIL y SUPABASE_PASSWORD en .env.")
        if codigo == 403:
            return ErrorPermiso("El usuario no tiene permiso para leer la tabla. "
                                "Revisa la política RLS en Supabase.")
        return super()._error_del_cliente(codigo, ruta)
