"""Pruebas del cliente externo con respuestas simuladas, sin usar la red."""

import os
import unittest
from unittest.mock import patch

import requests

from infraestructura.configuracion import ErrorDeConfiguracion
from servicios.cliente_supabase import ClienteSupabase
from servicios.excepciones import (ErrorAutenticacion, ErrorCertificado, ErrorPermiso,
                                   PeticionRechazada, RecursoNoEncontrado,
                                   RespuestaInvalida, ServicioNoDisponible)

URL = "https://proyecto.supabase.co"
TOKEN = {"access_token": "token_1", "refresh_token": "refresh_1"}


class RespuestaFalsa:
    """Imita lo que usa el cliente de requests.Response."""

    def __init__(self, codigo, datos=None, encabezados=None, texto=None):
        self.status_code = codigo
        self._datos = datos
        self.headers = encabezados or {}
        self._texto = texto

    def json(self):
        if self._texto is not None:
            raise requests.exceptions.JSONDecodeError("inválido", self._texto, 0)
        return self._datos


class SesionFalsa:
    """Devuelve o lanza, en orden, cada resultado programado y guarda las llamadas."""

    def __init__(self, *resultados):
        self.resultados = list(resultados)
        self.llamadas = []

    def request(self, metodo, url, **opciones):
        self.llamadas.append((metodo, url, opciones))
        resultado = self.resultados.pop(0)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado


class PruebasClienteSupabase(unittest.TestCase):
    """Comprueba timeout, códigos de estado, reintentos y renovación del token."""

    def crear(self, *resultados, intentos=4):
        self.sesion = SesionFalsa(*resultados)
        self.esperas = []
        return ClienteSupabase(URL, "clave_publica", "rodolfo@ejemplo.cl", "password",
                               sesion=self.sesion, esperar=self.esperas.append,
                               azar=lambda minimo, maximo: 0, maximo_intentos=intentos)

    def test_consulta_exitosa_usa_bearer_apikey_y_timeout(self):
        cliente = self.crear(RespuestaFalsa(200, TOKEN), RespuestaFalsa(200, [{"rut": "1"}]))
        self.assertEqual(cliente.obtener_empleados(), [{"rut": "1"}])
        for _, _, opciones in self.sesion.llamadas:
            self.assertEqual(opciones["timeout"], (3, 10))
            self.assertEqual(opciones["headers"]["apikey"], "clave_publica")
            self.assertNotIn("verify", opciones)
        metodo, url, opciones = self.sesion.llamadas[1]
        self.assertEqual((metodo, url), ("GET", URL + "/rest/v1/empleados"))
        self.assertEqual(opciones["headers"]["Authorization"], "Bearer token_1")
        self.assertNotIn("Authorization", self.sesion.llamadas[0][2]["headers"])
        self.assertEqual(self.esperas, [])

    def test_401_renueva_el_token_una_vez_y_reintenta(self):
        cliente = self.crear(RespuestaFalsa(200, TOKEN), RespuestaFalsa(401),
                             RespuestaFalsa(200, {"access_token": "token_2"}),
                             RespuestaFalsa(200, []))
        with self.assertLogs("servicios.cliente_supabase", "WARNING"):
            self.assertEqual(cliente.obtener_empleados(), [])
        renovacion = self.sesion.llamadas[2]
        self.assertEqual(renovacion[2]["params"], {"grant_type": "refresh_token"})
        self.assertEqual(renovacion[2]["json"], {"refresh_token": "refresh_1"})
        self.assertEqual(self.sesion.llamadas[3][2]["headers"]["Authorization"], "Bearer token_2")

    def test_segundo_401_no_se_reintenta_otra_vez(self):
        cliente = self.crear(RespuestaFalsa(200, TOKEN), RespuestaFalsa(401),
                             RespuestaFalsa(200, {"access_token": "token_2"}),
                             RespuestaFalsa(401))
        with self.assertLogs("servicios.cliente_supabase", "WARNING"):
            with self.assertRaises(ErrorAutenticacion):
                cliente.obtener_empleados()
        self.assertEqual(len(self.sesion.llamadas), 4)

    def test_refresh_rechazado_vuelve_a_iniciar_sesion(self):
        cliente = self.crear(RespuestaFalsa(200, TOKEN), RespuestaFalsa(401),
                             RespuestaFalsa(400), RespuestaFalsa(200, {"access_token": "token_3"}),
                             RespuestaFalsa(200, []))
        with self.assertLogs("servicios.cliente_supabase", "WARNING"):
            self.assertEqual(cliente.obtener_empleados(), [])
        self.assertEqual(self.sesion.llamadas[3][2]["params"], {"grant_type": "password"})

    def test_credenciales_invalidas_no_se_reintentan(self):
        cliente = self.crear(RespuestaFalsa(400, {"error": "invalid_grant"}))
        with self.assertRaises(ErrorAutenticacion):
            cliente.obtener_empleados()
        self.assertEqual(len(self.sesion.llamadas), 1)

    def test_403_404_y_otros_4xx_no_se_reintentan(self):
        for codigo, excepcion in ((403, ErrorPermiso), (404, RecursoNoEncontrado),
                                  (400, PeticionRechazada), (422, PeticionRechazada)):
            with self.subTest(codigo=codigo):
                cliente = self.crear(RespuestaFalsa(200, TOKEN), RespuestaFalsa(codigo))
                with self.assertRaises(excepcion):
                    cliente.obtener_empleados()
                self.assertEqual(len(self.sesion.llamadas), 2)
                self.assertEqual(self.esperas, [])

    def test_5xx_se_reintenta_con_espera_creciente(self):
        cliente = self.crear(RespuestaFalsa(200, TOKEN), RespuestaFalsa(500),
                             RespuestaFalsa(502), RespuestaFalsa(503), RespuestaFalsa(200, []))
        with self.assertLogs("servicios.cliente_supabase", "WARNING") as registro:
            self.assertEqual(cliente.obtener_empleados(), [])
        self.assertEqual(self.esperas, [1, 2, 4])
        self.assertEqual(len([linea for linea in registro.output if "Intento" in linea]), 3)

    def test_timeout_agota_el_tope_de_cuatro_intentos(self):
        cliente = self.crear(RespuestaFalsa(200, TOKEN),
                             *[requests.exceptions.ReadTimeout()] * 4)
        with self.assertLogs("servicios.cliente_supabase", "WARNING"):
            with self.assertRaises(ServicioNoDisponible) as contexto:
                cliente.obtener_empleados()
        self.assertEqual(len(self.sesion.llamadas), 5)
        self.assertEqual(self.esperas, [1, 2, 4])
        self.assertIn("tiempo límite", str(contexto.exception))

    def test_sin_red_falla_de_forma_controlada(self):
        cliente = self.crear(*[requests.exceptions.ConnectionError()] * 4)
        with self.assertLogs("servicios.cliente_supabase", "WARNING"):
            with self.assertRaises(ServicioNoDisponible) as contexto:
                cliente.obtener_empleados()
        self.assertIn("conexión de red", str(contexto.exception))

    def test_429_respeta_retry_after(self):
        cliente = self.crear(RespuestaFalsa(200, TOKEN),
                             RespuestaFalsa(429, encabezados={"Retry-After": "7"}),
                             RespuestaFalsa(200, []))
        with self.assertLogs("servicios.cliente_supabase", "WARNING"):
            cliente.obtener_empleados()
        self.assertEqual(self.esperas, [7])

    def test_429_sin_retry_after_usa_espera_creciente(self):
        cliente = self.crear(RespuestaFalsa(200, TOKEN), RespuestaFalsa(429),
                             RespuestaFalsa(200, []))
        with self.assertLogs("servicios.cliente_supabase", "WARNING"):
            cliente.obtener_empleados()
        self.assertEqual(self.esperas, [1])

    def test_429_con_espera_excesiva_no_bloquea_el_programa(self):
        cliente = self.crear(RespuestaFalsa(200, TOKEN),
                             RespuestaFalsa(429, encabezados={"Retry-After": "3600"}))
        with self.assertRaises(ServicioNoDisponible):
            cliente.obtener_empleados()
        self.assertEqual(self.esperas, [])

    def test_certificado_invalido_no_se_reintenta(self):
        cliente = self.crear(requests.exceptions.SSLError())
        with self.assertLogs("servicios.cliente_supabase", "ERROR"):
            with self.assertRaises(ErrorCertificado):
                cliente.obtener_empleados()
        self.assertEqual(len(self.sesion.llamadas), 1)

    def test_respuestas_invalidas_se_informan(self):
        casos = ((RespuestaFalsa(200, texto="<html>"),), (RespuestaFalsa(200, {"sin": "token"}),),
                 (RespuestaFalsa(200, TOKEN), RespuestaFalsa(200, {"no": "es lista"})))
        for resultados in casos:
            with self.subTest(resultados=resultados):
                with self.assertRaises(RespuestaInvalida):
                    self.crear(*resultados).obtener_empleados()

    def test_configuracion_insegura_o_incompleta_tiene_mensaje_claro(self):
        with self.assertRaises(ErrorDeConfiguracion):
            ClienteSupabase("http://proyecto.supabase.co", "clave", "correo", "clave")
        with self.assertRaises(ErrorDeConfiguracion):
            ClienteSupabase(URL, "clave", "correo", "clave", tabla="empleados?select=*")
        vacio = {"SUPABASE_URL": "", "SUPABASE_KEY": "", "SUPABASE_EMAIL": "",
                 "SUPABASE_PASSWORD": ""}
        with patch.dict(os.environ, vacio):
            with self.assertRaises(ErrorDeConfiguracion) as contexto:
                ClienteSupabase.desde_entorno()
        self.assertIn("SUPABASE_URL", str(contexto.exception))
        with patch.dict(os.environ, dict(vacio, SUPABASE_URL=URL)):
            with self.assertRaises(ErrorDeConfiguracion) as contexto:
                ClienteSupabase.desde_entorno()
        for nombre in ("SUPABASE_KEY", "SUPABASE_EMAIL", "SUPABASE_PASSWORD"):
            self.assertIn(nombre, str(contexto.exception))

    def test_sin_variables_informa_las_cuatro_juntas(self):
        vacio = {"SUPABASE_URL": "", "SUPABASE_KEY": "", "SUPABASE_EMAIL": "",
                 "SUPABASE_PASSWORD": ""}
        with patch.dict(os.environ, vacio):
            with self.assertRaises(ErrorDeConfiguracion) as contexto:
                ClienteSupabase.desde_entorno()
        for nombre in vacio:
            self.assertIn(nombre, str(contexto.exception))

    def test_rechaza_la_clave_secreta(self):
        entorno = {"SUPABASE_URL": URL, "SUPABASE_KEY": "sb_secret_abc123",
                   "SUPABASE_EMAIL": "prueba@user.com", "SUPABASE_PASSWORD": "clave"}
        with patch.dict(os.environ, entorno):
            with self.assertRaises(ErrorDeConfiguracion) as contexto:
                ClienteSupabase.desde_entorno()
        self.assertIn("clave secreta", str(contexto.exception))

    def test_el_log_no_contiene_tokens_ni_contrasenias(self):
        cliente = self.crear(RespuestaFalsa(200, TOKEN), RespuestaFalsa(500),
                             RespuestaFalsa(200, []))
        with self.assertLogs("servicios.cliente_supabase", "INFO") as registro:
            cliente.obtener_empleados()
        texto = "\n".join(registro.output)
        for secreto in ("token_1", "refresh_1", "password", "clave_publica"):
            self.assertNotIn(secreto, texto)


if __name__ == "__main__":
    unittest.main()
