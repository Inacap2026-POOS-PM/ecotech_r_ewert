"""Deserialización, persistencia y degradación controlada con una base temporal."""

import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from dominio.departamento import Departamento
from dominio.empleado_contratista import EmpleadoContratista
from dominio.empleado_planta import EmpleadoPlanta
from dominio.proyecto import Proyecto
from dominio.registro_tiempo import RegistroTiempo
from infraestructura.configuracion import ErrorDeConfiguracion
from infraestructura.conexion import obtener_conexion
from infraestructura.departamento_repositorio import DepartamentoRepositorio
from infraestructura.empleado_repositorio import EmpleadoRepositorio
from infraestructura.esquema import inicializar_bd
from infraestructura.proyecto_repositorio import ProyectoRepositorio
from infraestructura.registro_tiempo_repositorio import RegistroTiempoRepositorio
from servicios.deserializador_empleados import DeserializadorEmpleados
from servicios.excepciones import RespuestaInvalida, ServicioNoDisponible
from servicios.sincronizador import (API, LOCAL, SIN_DATOS, SincronizadorEmpleados,
                                     describir_antiguedad)

PLANTA = {"rut": "15234567-4", "nombre": "Camila Soto", "fecha_ingreso": "2023-03-01",
          "sueldo_base": 1100000, "tipo_contrato": "planta", "bono": 80000,
          "email": "camila@ecotech.cl", "telefono": "+56911112222", "direccion": "Av. Sol 10"}
CONTRATISTA = {"rut": "18765432-7", "nombre": "Diego Pérez", "fecha_ingreso": "2024-07-15",
               "sueldo_base": 900000, "tipo_contrato": "contratista", "tarifa_hora": 12000,
               "email": None, "telefono": None, "direccion": None}
INYECCION = "'; DROP TABLE empleado;--"


class PruebasDeserializador(unittest.TestCase):
    """El JSON externo se trata como un dato hostil."""

    def setUp(self):
        self.deserializador = DeserializadorEmpleados()

    def test_convierte_a_objetos_del_dominio_tipados(self):
        resultado = self.deserializador.deserializar([PLANTA, CONTRATISTA])
        planta, contratista = resultado.empleados
        self.assertIsInstance(planta, EmpleadoPlanta)
        self.assertIsInstance(contratista, EmpleadoContratista)
        self.assertEqual(planta.fecha_ingreso, date(2023, 3, 1))
        self.assertEqual(planta.calcular_pago(), 1180000)
        self.assertEqual(contratista.tarifa_hora, 12000)
        self.assertEqual(contratista.email, "")
        self.assertEqual(resultado.rechazados, [])

    def test_datos_hostiles_se_rechazan_con_su_motivo(self):
        hostiles = [
            dict(PLANTA, rut="15234567-9"),
            dict(PLANTA, rut="ECO-12345"),
            dict(PLANTA, rut=INYECCION),
            dict(PLANTA, email="no-es-correo"),
            dict(PLANTA, sueldo_base="1100000"),
            dict(PLANTA, sueldo_base=True),
            dict(PLANTA, sueldo_base=-5),
            dict(PLANTA, fecha_ingreso="01/03/2023"),
            dict(PLANTA, fecha_ingreso="2023-02-30"),
            dict(PLANTA, fecha_ingreso="2999-01-01"),
            dict(PLANTA, tipo_contrato="gerente"),
            dict(PLANTA, nombre="x" * 101),
            dict(PLANTA, nombre=None),
            {k: v for k, v in CONTRATISTA.items() if k != "tarifa_hora"},
            {"rut": None},
            "no soy un objeto",
            None,
            PLANTA,
            PLANTA,
        ]
        resultado = self.deserializador.deserializar(hostiles)
        self.assertEqual(len(resultado.empleados), 1)
        self.assertEqual(len(resultado.rechazados), len(hostiles) - 1)
        self.assertIn("repetido", resultado.rechazados[-1].motivo)
        for rechazo in resultado.rechazados:
            self.assertTrue(rechazo.motivo)
            self.assertLessEqual(len(rechazo.rut), 12)

    def test_estructura_invalida_de_la_respuesta(self):
        for datos in ({"empleados": []}, None, "texto", [PLANTA] * 1001):
            with self.subTest(tipo=type(datos).__name__):
                with self.assertRaises(RespuestaInvalida):
                    self.deserializador.deserializar(datos)


class ClienteFalso:
    """Reemplaza a Supabase y devuelve datos o lanza el error indicado."""

    def __init__(self, datos=None, error=None):
        self.datos = datos
        self.error = error

    def obtener_empleados(self):
        if self.error:
            raise self.error
        return self.datos


class PruebasSincronizacion(unittest.TestCase):
    """Upsert parametrizado, obtenido_en y planes B y C."""

    def setUp(self):
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        entorno = patch.dict(os.environ, {
            "DB_NOMBRE": str(Path(temporal.name) / "sincronizacion.db"), "DB_MOTOR": "sqlite",
            "CLAVE_CIFRADO": Fernet.generate_key().decode("ascii")})
        entorno.start()
        self.addCleanup(entorno.stop)
        inicializar_bd()
        self.repo = EmpleadoRepositorio()
        self.ahora = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)

    def sincronizador(self, cliente, ahora=None):
        return SincronizadorEmpleados(lambda: cliente, repositorio=self.repo,
                                      reloj=lambda: ahora or self.ahora)

    def test_sincronizacion_inserta_y_luego_actualiza(self):
        with self.assertLogs("servicios.sincronizador", "INFO"):
            resultado = self.sincronizador(ClienteFalso([PLANTA, CONTRATISTA])).sincronizar()
        self.assertEqual(resultado.origen, API)
        self.assertEqual(len(resultado.empleados), 2)
        self.assertEqual(self.repo.obtener("15234567-4").email, "camila@ecotech.cl")
        despues = self.ahora + timedelta(hours=1)
        with self.assertLogs("servicios.sincronizador", "INFO"):
            self.sincronizador(ClienteFalso([dict(PLANTA, sueldo_base=1200000)]),
                               despues).sincronizar()
        self.assertEqual(self.repo.obtener("15234567-4").sueldo_base, 1200000)
        with obtener_conexion() as conn:
            filas = dict(conn.execute("SELECT rut, obtenido_en FROM empleado").fetchall())
            email = conn.execute("SELECT email FROM empleado WHERE rut = ?",
                                 ("15234567-4",)).fetchone()[0]
        self.assertEqual(filas["15234567-4"], despues.isoformat())
        self.assertEqual(filas["18765432-7"], self.ahora.isoformat())
        self.assertTrue(email.startswith("fernet:"))

    def test_nombre_con_inyeccion_se_guarda_como_texto(self):
        with self.assertLogs("servicios.sincronizador", "INFO"):
            self.sincronizador(ClienteFalso([dict(PLANTA, nombre=INYECCION)])).sincronizar()
        self.assertEqual(self.repo.obtener("15234567-4").nombre, INYECCION)
        with obtener_conexion() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM empleado").fetchone()[0], 1)

    def test_sincronizar_conserva_departamento_y_jornadas_locales(self):
        empleado = EmpleadoPlanta("15234567-4", "Camila", date(2023, 3, 1), 1000000)
        DepartamentoRepositorio().guardar(Departamento("Energía")).asignar_empleado(empleado)
        self.repo.guardar(empleado)
        proyecto = ProyectoRepositorio().guardar(Proyecto(None, "Eco", date(2024, 1, 1)))
        self.repo.asignar_proyecto(empleado, proyecto)
        registro = RegistroTiempo(date(2024, 2, 1), 5, "Diseño", empleado, proyecto)
        registro.ingresar_registro()
        RegistroTiempoRepositorio().guardar(registro)
        with self.assertLogs("servicios.sincronizador", "INFO"):
            resultado = self.sincronizador(ClienteFalso(
                [PLANTA, dict(CONTRATISTA, rut="15234567-4", fecha_ingreso="2025-01-01")])
            ).sincronizar()
        self.assertEqual(len(resultado.empleados), 1)
        self.assertEqual(len(resultado.rechazados), 1)
        actualizado = self.repo.obtener("15234567-4")
        self.assertEqual(actualizado.total_horas(), 5)
        self.assertEqual(actualizado.departamento.nombre, "Energía")
        self.assertEqual(actualizado.bono_sostenibilidad, 80000)
        with self.assertLogs("servicios.sincronizador", "INFO"):
            resultado = self.sincronizador(ClienteFalso(
                [dict(PLANTA, fecha_ingreso="2025-01-01")])).sincronizar()
        self.assertIn("jornadas", resultado.rechazados[0].motivo)
        self.assertEqual(self.repo.obtener("15234567-4").fecha_ingreso, date(2023, 3, 1))

    def test_plan_b_usa_datos_locales_e_indica_su_antiguedad(self):
        with self.assertLogs("servicios.sincronizador", "INFO"):
            self.sincronizador(ClienteFalso([PLANTA])).sincronizar()
        caida = ClienteFalso(error=ServicioNoDisponible("sin red"))
        with self.assertLogs("servicios.sincronizador", "ERROR"):
            resultado = self.sincronizador(caida).sincronizar()
        self.assertEqual(resultado.origen, LOCAL)
        self.assertEqual([e.rut for e in resultado.empleados], ["15234567-4"])
        self.assertEqual(resultado.obtenido_en, self.ahora)
        self.assertIn("sin red", resultado.motivo)
        self.assertEqual(describir_antiguedad(resultado.obtenido_en,
                                              self.ahora + timedelta(hours=2, minutes=5)),
                         "hace 2 h 5 min")

    def test_plan_b_tambien_cubre_configuracion_o_json_invalidos(self):
        with self.assertLogs("servicios.sincronizador", "INFO"):
            self.sincronizador(ClienteFalso([PLANTA])).sincronizar()
        for cliente in (ClienteFalso(error=ErrorDeConfiguracion("Faltan variables")),
                        ClienteFalso({"no": "lista"})):
            with self.assertLogs("servicios.sincronizador", "ERROR"):
                self.assertEqual(self.sincronizador(cliente).sincronizar().origen, LOCAL)

    def test_plan_c_sin_datos_locales_lo_informa(self):
        self.repo.guardar(EmpleadoPlanta("16543210-K", "Local", date(2024, 1, 1), 900000))
        with self.assertLogs("servicios.sincronizador", "ERROR"):
            resultado = self.sincronizador(
                ClienteFalso(error=ServicioNoDisponible("sin red"))).sincronizar()
        self.assertEqual(resultado.origen, SIN_DATOS)
        self.assertEqual(resultado.empleados, [])
        self.assertIn("sin red", resultado.motivo)

    def test_esquema_antiguo_recibe_la_columna_obtenido_en(self):
        with obtener_conexion() as conn:
            columnas = [fila[1] for fila in conn.execute("PRAGMA table_info(empleado)")]
        self.assertIn("obtenido_en", columnas)


if __name__ == "__main__":
    unittest.main()
