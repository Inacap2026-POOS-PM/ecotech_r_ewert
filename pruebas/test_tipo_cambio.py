"""Tipo de cambio del dólar: validación, cliente, deserializador y planes B y C."""

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

from dominio.tipo_cambio import TipoCambio
from dominio.validador_entradas import ValidadorEntradas
from infraestructura.configuracion import ErrorDeConfiguracion
from infraestructura.conexion import obtener_conexion
from infraestructura.esquema import inicializar_bd
from infraestructura.tipo_cambio_repositorio import TipoCambioRepositorio
from main import menu
from servicios.cliente_tipo_cambio import ClienteTipoCambio
from servicios.deserializador_tipo_cambio import DeserializadorTipoCambio
from servicios.excepciones import (ErrorCertificado, PeticionRechazada, RespuestaInvalida,
                                   ServicioNoDisponible)
from servicios.servicio_tipo_cambio import ServicioTipoCambio
from servicios.sincronizador import API, LOCAL, SIN_DATOS, describir_antiguedad
from test_cliente_supabase import RespuestaFalsa, SesionFalsa

ACTUALIZADO = 1795046551  # 19-11-2026 00:02:31 UTC
TASAS = {"result": "success", "base_code": "USD", "time_last_update_unix": ACTUALIZADO,
         "rates": {"USD": 1, "CLP": 993.467816, "EUR": 0.86}}
MONTOS_HOSTILES = ["", "   ", "abc", "-5", "0", "0,00", "1e9", "nan", "inf", "10.999",
                   "100; DROP TABLE tipo_cambio;--", "1.000.000", "99999999999", None, 15]


class PruebasValidacion(unittest.TestCase):
    """Las entradas se validan antes de emitir la petición HTTP."""

    def test_montos_validos(self):
        self.assertEqual(ValidadorEntradas.monto(" 150 "), 150)
        self.assertEqual(ValidadorEntradas.monto("99,90"), 99.9)
        self.assertEqual(ValidadorEntradas.monto("0.5"), 0.5)

    def test_montos_hostiles_se_rechazan_sin_llamar_a_la_api(self):
        def no_debe_llamarse():
            raise AssertionError("Se intentó llamar a la API con una entrada inválida.")
        servicio = ServicioTipoCambio(crear_cliente=no_debe_llamarse, repositorio=Mock())
        for entrada in MONTOS_HOSTILES:
            with self.subTest(entrada=entrada):
                with self.assertRaises(ValueError):
                    servicio.consultar(entrada)

    def test_codigo_de_moneda(self):
        self.assertEqual(ValidadorEntradas.codigo_moneda(" usd "), "USD")
        for codigo in ("US", "USDT", "U$D", "../CLP", "USD/../../x", ""):
            with self.subTest(codigo=codigo):
                with self.assertRaises(ValueError):
                    ValidadorEntradas.codigo_moneda(codigo)


class PruebasClienteTipoCambio(unittest.TestCase):
    """Cliente con sesión simulada: no usa la red."""

    def crear(self, *resultados):
        self.sesion = SesionFalsa(*resultados)
        self.esperas = []
        return ClienteTipoCambio(sesion=self.sesion, esperar=self.esperas.append,
                                 azar=lambda minimo, maximo: 0)

    def test_consulta_usa_https_timeout_y_la_moneda_en_la_ruta(self):
        cliente = self.crear(RespuestaFalsa(200, TASAS))
        self.assertEqual(cliente.obtener_tasas("usd"), TASAS)
        metodo, url, opciones = self.sesion.llamadas[0]
        self.assertEqual((metodo, url), ("GET", "https://open.er-api.com/v6/latest/USD"))
        self.assertEqual(opciones["timeout"], (3, 10))

    def test_moneda_invalida_no_llega_a_la_red(self):
        cliente = self.crear()
        with self.assertRaises(ValueError):
            cliente.obtener_tasas("../../admin")
        self.assertEqual(self.sesion.llamadas, [])

    def test_5xx_y_red_se_reintentan_con_espera_creciente(self):
        caida = requests.exceptions.ConnectionError("sin red")
        cliente = self.crear(RespuestaFalsa(503), caida, RespuestaFalsa(200, TASAS))
        with self.assertLogs("servicios.cliente_tipo_cambio", "WARNING") as registro:
            self.assertEqual(cliente.obtener_tasas(), TASAS)
        self.assertEqual(self.esperas, [1, 2])
        self.assertIn("Intento 1/4", registro.output[0])

    def test_429_respeta_retry_after(self):
        cliente = self.crear(RespuestaFalsa(429, encabezados={"Retry-After": "7"}),
                             RespuestaFalsa(200, TASAS))
        with self.assertLogs("servicios.cliente_tipo_cambio", "WARNING"):
            cliente.obtener_tasas()
        self.assertEqual(self.esperas, [7])

    def test_tope_de_cuatro_intentos(self):
        cliente = self.crear(*[requests.exceptions.Timeout()] * 4)
        with self.assertLogs("servicios.cliente_tipo_cambio", "WARNING"):
            with self.assertRaises(ServicioNoDisponible) as error:
                cliente.obtener_tasas()
        self.assertEqual(len(self.sesion.llamadas), 4)
        self.assertIn("ExchangeRate-API no está disponible", str(error.exception))

    def test_4xx_y_tls_no_se_reintentan(self):
        cliente = self.crear(RespuestaFalsa(400))
        with self.assertRaises(PeticionRechazada):
            cliente.obtener_tasas()
        cliente = self.crear(requests.exceptions.SSLError("certificado falso"))
        with self.assertLogs("servicios.cliente_tipo_cambio", "ERROR"):
            with self.assertRaises(ErrorCertificado):
                cliente.obtener_tasas()
        self.assertEqual(self.esperas, [])

    def test_url_sin_https_se_rechaza(self):
        with self.assertRaises(ErrorDeConfiguracion):
            ClienteTipoCambio(url="http://open.er-api.com/v6/latest")


class PruebasDeserializador(unittest.TestCase):
    """El JSON de la API se trata como un dato hostil."""

    def setUp(self):
        self.deserializador = DeserializadorTipoCambio()

    def test_convierte_a_un_tipo_de_cambio_tipado(self):
        tipo_cambio = self.deserializador.convertir(TASAS, "USD", "CLP")
        self.assertIsInstance(tipo_cambio, TipoCambio)
        self.assertEqual(tipo_cambio.valor, 993.467816)
        self.assertEqual(tipo_cambio.actualizado_en,
                         datetime(2026, 11, 19, 0, 2, 31, tzinfo=timezone.utc))
        self.assertAlmostEqual(tipo_cambio.convertir(100), 99346.7816)

    def test_error_con_codigo_200_se_detecta(self):
        with self.assertRaises(RespuestaInvalida) as error:
            self.deserializador.convertir({"result": "error", "error-type": "unsupported-code"},
                                          "USD", "CLP")
        self.assertIn("unsupported-code", str(error.exception))

    def test_datos_hostiles_se_rechazan(self):
        tasas = TASAS["rates"]
        hostiles = [None, [], "texto", {},
                    dict(TASAS, base_code="EUR"),
                    dict(TASAS, rates="CLP=993"),
                    dict(TASAS, rates={"USD": 1}),
                    dict(TASAS, rates=dict(tasas, CLP="993")),
                    dict(TASAS, rates=dict(tasas, CLP=True)),
                    dict(TASAS, rates=dict(tasas, CLP=-1)),
                    dict(TASAS, rates=dict(tasas, CLP=0)),
                    dict(TASAS, rates=dict(tasas, CLP=float("inf"))),
                    dict(TASAS, rates=dict(tasas, CLP=None)),
                    dict(TASAS, time_last_update_unix="hoy"),
                    dict(TASAS, time_last_update_unix=4102444800)]
        for datos in hostiles:
            with self.subTest(datos=str(datos)[:60]):
                with self.assertRaises(RespuestaInvalida):
                    self.deserializador.convertir(datos, "USD", "CLP")


class ClienteFalso:
    """Reemplaza a la API y devuelve datos o lanza el error indicado."""

    def __init__(self, datos=TASAS, error=None):
        self.datos, self.error = datos, error

    def obtener_tasas(self, moneda_base):
        if self.error:
            raise self.error
        return self.datos


class PruebasServicioTipoCambio(unittest.TestCase):
    """Upsert parametrizado con obtenido_en y degradación controlada."""

    def setUp(self):
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        entorno = patch.dict(os.environ, {
            "DB_NOMBRE": str(Path(temporal.name) / "dolar.db"), "DB_MOTOR": "sqlite",
            "CLAVE_CIFRADO": Fernet.generate_key().decode("ascii")})
        entorno.start()
        self.addCleanup(entorno.stop)
        inicializar_bd()
        self.ahora = datetime(2026, 11, 19, 19, 15, tzinfo=timezone.utc)

    def servicio(self, cliente, ahora=None):
        return ServicioTipoCambio(lambda: cliente, repositorio=TipoCambioRepositorio(),
                                  reloj=lambda: ahora or self.ahora)

    def test_plan_a_convierte_guarda_y_actualiza_sin_duplicar(self):
        with self.assertLogs("servicios.servicio_tipo_cambio", "INFO"):
            resultado = self.servicio(ClienteFalso()).consultar("100")
        self.assertEqual(resultado.origen, API)
        self.assertAlmostEqual(resultado.total(), 99346.7816)
        nuevo = dict(TASAS, rates={"USD": 1, "CLP": 1000.5})
        despues = self.ahora + timedelta(hours=1)
        with self.assertLogs("servicios.servicio_tipo_cambio", "INFO"):
            self.servicio(ClienteFalso(nuevo), despues).consultar("1")
        with obtener_conexion() as conn:
            filas = conn.execute("SELECT moneda_base, moneda_destino, valor, obtenido_en "
                                 "FROM tipo_cambio").fetchall()
        self.assertEqual(filas, [("USD", "CLP", 1000.5, despues.isoformat())])

    def test_plan_b_usa_el_ultimo_valor_y_su_antiguedad(self):
        with self.assertLogs("servicios.servicio_tipo_cambio", "INFO"):
            self.servicio(ClienteFalso()).consultar("1")
        caida = ClienteFalso(error=ServicioNoDisponible("sin red"))
        with self.assertLogs("servicios.servicio_tipo_cambio", "ERROR"):
            resultado = self.servicio(caida).consultar("10")
        self.assertEqual(resultado.origen, LOCAL)
        self.assertAlmostEqual(resultado.total(), 9934.67816)
        self.assertEqual(resultado.obtenido_en, self.ahora)
        self.assertIn("sin red", resultado.motivo)
        self.assertEqual(describir_antiguedad(resultado.obtenido_en,
                                              self.ahora + timedelta(minutes=30)), "hace 30 min")

    def test_plan_b_tambien_cubre_respuesta_invalida(self):
        with self.assertLogs("servicios.servicio_tipo_cambio", "INFO"):
            self.servicio(ClienteFalso()).consultar("1")
        corrupta = ClienteFalso({"result": "error", "error-type": "quota-reached"})
        with self.assertLogs("servicios.servicio_tipo_cambio", "ERROR"):
            self.assertEqual(self.servicio(corrupta).consultar("1").origen, LOCAL)

    def test_plan_c_sin_datos_guardados(self):
        caida = ClienteFalso(error=ServicioNoDisponible("sin red"))
        with self.assertLogs("servicios.servicio_tipo_cambio", "ERROR"):
            resultado = self.servicio(caida).consultar("1")
        self.assertEqual(resultado.origen, SIN_DATOS)
        self.assertIsNone(resultado.total())

    def test_menu_opcion_12_falla_limpio_ante_entrada_hostil(self):
        gestor = Mock()
        gestor.verificar_rol.return_value = True
        salida = io.StringIO()
        with redirect_stdout(salida):
            with patch("builtins.input", side_effect=["12", "100; DROP TABLE x;--", "0"]):
                menu(gestor)
        texto = salida.getvalue()
        self.assertIn("El monto debe ser un número positivo", texto)
        self.assertNotIn("Traceback", texto)
        gestor.verificar_rol.assert_called_with(gestor.usuario_actual, "consulta")


if __name__ == "__main__":
    unittest.main()
