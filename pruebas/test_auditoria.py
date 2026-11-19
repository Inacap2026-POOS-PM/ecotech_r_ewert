"""El auditor debe encontrar las señales en código vulnerable y ninguna en el proyecto."""

import unittest
from unittest.mock import Mock

import requests

from auditoria import auditar_codigo, auditar_proyecto, verificar_pypi

VULNERABLE = '''
import requests
def malo(conn, rut, sesion):
    conn.execute(f"SELECT * FROM empleado WHERE rut = '{rut}'")
    sql = "DELETE FROM persona WHERE rut = '%s'" % rut
    conn.execute("UPDATE persona SET nombre = '{}'".format(rut))
    requests.get("https://api.ejemplo.cl", verify=False)
    sesion.verify = False
    sesion.post("https://api.ejemplo.cl", timeout=10)
    try:
        sesion.request("GET", "https://api.ejemplo.cl")
    except Exception:
        pass
'''

SEGURO = '''
def bien(conn, nombre, registro):
    conn.execute("SELECT * FROM persona WHERE nombre LIKE ?", (f"%{nombre}%",))
    print(f"Eliminar a {nombre}")
    registro.get("rut")
    try:
        conn.close()
    except ValueError as error:
        raise RuntimeError("falló") from error
'''


class PruebasAuditoria(unittest.TestCase):
    """Verifica las cinco señales de la pauta."""

    def test_detecta_cada_senal_en_codigo_vulnerable(self):
        senales = [hallazgo[0] for hallazgo in auditar_codigo(VULNERABLE)]
        self.assertEqual(senales.count("sql"), 3)
        self.assertEqual(senales.count("tls"), 2)
        self.assertEqual(senales.count("timeout"), 2)
        self.assertEqual(senales.count("except"), 1)

    def test_no_marca_codigo_seguro(self):
        self.assertEqual(auditar_codigo(SEGURO), [])

    def test_el_proyecto_no_tiene_hallazgos(self):
        self.assertEqual(auditar_proyecto(), [])

    def test_pypi_distingue_existe_no_existe_y_sin_red(self):
        sesion = Mock()
        sesion.get.side_effect = [Mock(status_code=200), Mock(status_code=404),
                                  requests.exceptions.ConnectionError()]
        resultados = verificar_pypi([("requests", "2.34.2"), ("reqeusts", "1.0"),
                                     ("bcrypt", "5.0.0")], sesion)
        self.assertEqual([r[2] for r in resultados], ["existe", "no existe", "no verificado"])
        for llamada in sesion.get.call_args_list:
            self.assertEqual(llamada.kwargs["timeout"], (3, 10))


if __name__ == "__main__":
    unittest.main()
