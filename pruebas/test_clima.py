"""Clima con Open-Meteo: validación de la entrada, cliente, deserializador y planes B y C."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from cryptography.fernet import Fernet

from dominio.validador_entradas import ValidadorEntradas
from infraestructura.clima_repositorio import ClimaRepositorio
from infraestructura.configuracion import ErrorDeConfiguracion
from infraestructura.conexion import obtener_conexion
from infraestructura.esquema import inicializar_bd
from main import menu
from servicios.cliente_clima import ClienteClima
from servicios.deserializador_clima import CiudadNoEncontrada, DeserializadorClima
from servicios.excepciones import (ErrorCertificado, RecursoNoEncontrado, RespuestaInvalida,
                                   ServicioNoDisponible)
from servicios.servicio_clima import ServicioClima
from servicios.sincronizador import API, LOCAL, SIN_DATOS, describir_antiguedad
from test_cliente_supabase import RespuestaFalsa, SesionFalsa

GEO = {"results": [{"name": "Temuco", "admin1": "Región de la Araucanía",
                    "latitude": -38.73628, "longitude": -72.59738}]}
ACTUAL = {"current": {"time": "2026-11-19T16:15", "temperature_2m": 23.1,
                      "relative_humidity_2m": 39, "wind_speed_10m": 7.5, "weather_code": 1}}
HOSTILES = ["", "   ", "T", "Temuco; DROP TABLE clima;--", "../../etc/passwd",
            "<script>alert(1)</script>", "12345", "x" * 61, "Temuco\nSantiago", None, 42]


class PruebasValidacionCiudad(unittest.TestCase):
    """La entrada del usuario se valida antes de emitir la petición HTTP."""

    def test_ciudades_validas_se_sanean(self):
        self.assertEqual(ValidadorEntradas.ciudad("  Puerto   Montt "), "Puerto Montt")
        for ciudad in ("Viña del Mar", "O'Higgins", "Ñuñoa", "Los Ángeles", "Santa Cruz-Norte"):
            self.assertEqual(ValidadorEntradas.ciudad(ciudad), ciudad)

    def test_entradas_hostiles_se_rechazan_sin_llamar_a_la_api(self):
        def no_debe_llamarse():
            raise AssertionError("Se intentó llamar a la API con una entrada inválida.")
        servicio = ServicioClima(crear_cliente=no_debe_llamarse, repositorio=Mock())
        for entrada in HOSTILES:
            with self.subTest(entrada=entrada):
                with self.assertRaises(ValueError):
                    servicio.consultar(entrada)


class PruebasClienteClima(unittest.TestCase):
    """Cliente con sesión simulada: no usa la red."""

    def crear(self, *resultados):
        self.sesion = SesionFalsa(*resultados)
        self.esperas = []
        return ClienteClima(sesion=self.sesion, esperar=self.esperas.append,
                            azar=lambda minimo, maximo: 0)

    def test_la_ciudad_viaja_en_params_con_timeout_y_https(self):
        cliente = self.crear(RespuestaFalsa(200, GEO))
        self.assertEqual(cliente.buscar_ciudad("Temuco"), GEO)
        metodo, url, opciones = self.sesion.llamadas[0]
        self.assertEqual(metodo, "GET")
        self.assertTrue(url.startswith("https://"))
        self.assertNotIn("Temuco", url)
        self.assertEqual(opciones["params"]["name"], "Temuco")
        self.assertEqual(opciones["timeout"], (3, 10))

    def test_5xx_y_red_se_reintentan_con_espera_creciente(self):
        caida = requests.exceptions.ConnectionError("sin red")
        cliente = self.crear(RespuestaFalsa(503), caida, RespuestaFalsa(200, ACTUAL))
        with self.assertLogs("servicios.cliente_clima", "WARNING") as registro:
            self.assertEqual(cliente.obtener_clima_actual(-38.7, -72.6), ACTUAL)
        self.assertEqual(self.esperas, [1, 2])
        self.assertIn("Intento 1/4", registro.output[0])

    def test_tope_de_cuatro_intentos(self):
        cliente = self.crear(*[requests.exceptions.Timeout()] * 4)
        with self.assertLogs("servicios.cliente_clima", "WARNING"):
            with self.assertRaises(ServicioNoDisponible) as error:
                cliente.obtener_clima_actual(-38.7, -72.6)
        self.assertEqual(len(self.sesion.llamadas), 4)
        self.assertIn("Open-Meteo no está disponible", str(error.exception))

    def test_404_y_tls_no_se_reintentan(self):
        cliente = self.crear(RespuestaFalsa(404))
        with self.assertRaises(RecursoNoEncontrado):
            cliente.buscar_ciudad("Temuco")
        cliente = self.crear(requests.exceptions.SSLError("certificado falso"))
        with self.assertLogs("servicios.cliente_clima", "ERROR"):
            with self.assertRaises(ErrorCertificado):
                cliente.buscar_ciudad("Temuco")
        self.assertEqual(self.esperas, [])

    def test_url_sin_https_se_rechaza(self):
        with self.assertRaises(ErrorDeConfiguracion):
            ClienteClima(url_pronostico="http://api.open-meteo.com/v1/forecast")


class PruebasDeserializadorClima(unittest.TestCase):
    """El JSON de Open-Meteo se trata como un dato hostil."""

    def setUp(self):
        self.deserializador = DeserializadorClima()
        self.ubicacion = self.deserializador.ubicacion(GEO, "Temuco")

    def test_convierte_a_un_registro_tipado(self):
        registro = self.deserializador.clima(ACTUAL, self.ubicacion)
        self.assertEqual(registro.ciudad, "Temuco")
        self.assertEqual(registro.temperatura, 23.1)
        self.assertEqual(registro.medido_en, datetime(2026, 11, 19, 16, 15))
        self.assertEqual(registro.descripcion(), "Mayormente despejado")
        self.assertIn("23.1 °C", registro.resumen())

    def test_ciudad_inexistente_no_es_una_falla_del_servicio(self):
        with self.assertRaises(CiudadNoEncontrada):
            self.deserializador.ubicacion({"generationtime_ms": 0.1}, "Atlantida")
        self.assertTrue(issubclass(CiudadNoEncontrada, ValueError))

    def test_datos_hostiles_se_rechazan(self):
        geo_hostiles = [None, [], {"results": "texto"}, {"results": [None]},
                        {"results": [dict(GEO["results"][0], latitude="-38")]},
                        {"results": [dict(GEO["results"][0], name=None)]}]
        for datos in geo_hostiles:
            with self.subTest(geo=datos):
                with self.assertRaises(RespuestaInvalida):
                    self.deserializador.ubicacion(datos, "Temuco")
        cambios = [{"temperature_2m": "23"}, {"temperature_2m": True},
                   {"relative_humidity_2m": 150}, {"wind_speed_10m": -1},
                   {"weather_code": 1000}, {"time": "19/11/2026"}, {"time": None},
                   {"temperature_2m": None}]
        for cambio in cambios:
            with self.subTest(cambio=cambio):
                with self.assertRaises(RespuestaInvalida):
                    self.deserializador.clima({"current": dict(ACTUAL["current"], **cambio)},
                                              self.ubicacion)
        with self.assertRaises(RespuestaInvalida):
            self.deserializador.clima({"otro": 1}, self.ubicacion)


class ClienteClimaFalso:
    """Reemplaza a Open-Meteo y devuelve datos o lanza el error indicado."""

    def __init__(self, geo=GEO, actual=ACTUAL, error=None):
        self.geo, self.actual, self.error = geo, actual, error

    def buscar_ciudad(self, ciudad):
        if self.error:
            raise self.error
        return self.geo

    def obtener_clima_actual(self, latitud, longitud):
        return self.actual


class PruebasServicioClima(unittest.TestCase):
    """Upsert parametrizado con obtenido_en y degradación controlada."""

    def setUp(self):
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        entorno = patch.dict(os.environ, {
            "DB_NOMBRE": str(Path(temporal.name) / "clima.db"), "DB_MOTOR": "sqlite",
            "CLAVE_CIFRADO": Fernet.generate_key().decode("ascii")})
        entorno.start()
        self.addCleanup(entorno.stop)
        inicializar_bd()
        self.ahora = datetime(2026, 11, 19, 19, 15, tzinfo=timezone.utc)

    def servicio(self, cliente, ahora=None):
        return ServicioClima(lambda: cliente, repositorio=ClimaRepositorio(),
                             reloj=lambda: ahora or self.ahora)

    def test_plan_a_guarda_y_actualiza_sin_duplicar(self):
        with self.assertLogs("servicios.servicio_clima", "INFO"):
            resultado = self.servicio(ClienteClimaFalso()).consultar("temuco")
        self.assertEqual(resultado.origen, API)
        nuevo = {"current": dict(ACTUAL["current"], temperature_2m=18.0)}
        despues = self.ahora + timedelta(hours=1)
        with self.assertLogs("servicios.servicio_clima", "INFO"):
            self.servicio(ClienteClimaFalso(actual=nuevo), despues).consultar("Temuco")
        with obtener_conexion() as conn:
            filas = conn.execute("SELECT consulta, temperatura, obtenido_en FROM clima").fetchall()
        self.assertEqual(filas, [("temuco", 18.0, despues.isoformat())])

    def test_plan_b_muestra_el_ultimo_clima_y_su_antiguedad(self):
        with self.assertLogs("servicios.servicio_clima", "INFO"):
            self.servicio(ClienteClimaFalso()).consultar("Temuco")
        caida = ClienteClimaFalso(error=ServicioNoDisponible("sin red"))
        with self.assertLogs("servicios.servicio_clima", "ERROR"):
            resultado = self.servicio(caida).consultar("TEMUCO")
        self.assertEqual(resultado.origen, LOCAL)
        self.assertEqual(resultado.registro.ciudad, "Temuco")
        self.assertEqual(resultado.obtenido_en, self.ahora)
        self.assertEqual(describir_antiguedad(resultado.obtenido_en,
                                              self.ahora + timedelta(minutes=30)), "hace 30 min")

    def test_plan_c_sin_datos_guardados(self):
        caida = ClienteClimaFalso(error=ServicioNoDisponible("sin red"))
        with self.assertLogs("servicios.servicio_clima", "ERROR"):
            resultado = self.servicio(caida).consultar("Temuco")
        self.assertEqual(resultado.origen, SIN_DATOS)
        self.assertIn("sin red", resultado.motivo)

    def test_menu_opcion_12_falla_limpio_ante_entrada_hostil(self):
        gestor = Mock()
        gestor.verificar_rol.return_value = True
        salida = io.StringIO()
        with redirect_stdout(salida):
            with patch("builtins.input", side_effect=["12", "Temuco; DROP TABLE clima;--", "0"]):
                menu(gestor)
        texto = salida.getvalue()
        self.assertIn("La ciudad solo admite letras", texto)
        self.assertNotIn("Traceback", texto)
        gestor.verificar_rol.assert_called_with(gestor.usuario_actual, "consulta")


if __name__ == "__main__":
    unittest.main()
