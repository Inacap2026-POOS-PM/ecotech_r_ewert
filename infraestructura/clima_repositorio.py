import logging
from datetime import datetime

from dominio.registro_clima import RegistroClima
from infraestructura.conexion import obtener_conexion

log = logging.getLogger(__name__)


class ClimaRepositorio:
    """Guarda el último clima de cada ciudad con consultas parametrizadas."""

    def guardar(self, consulta, registro, obtenido_en):
        """Inserta o actualiza la ciudad y registra cuándo se obtuvo el dato."""
        with obtener_conexion() as conn:
            conn.execute("""INSERT INTO clima
                (consulta, ciudad, region, latitud, longitud, temperatura, humedad, viento,
                 codigo_clima, medido_en, obtenido_en)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(consulta) DO UPDATE SET
                ciudad = excluded.ciudad, region = excluded.region,
                latitud = excluded.latitud, longitud = excluded.longitud,
                temperatura = excluded.temperatura, humedad = excluded.humedad,
                viento = excluded.viento, codigo_clima = excluded.codigo_clima,
                medido_en = excluded.medido_en, obtenido_en = excluded.obtenido_en""",
                (consulta.lower(), registro.ciudad, registro.region, registro.latitud,
                 registro.longitud, registro.temperatura, registro.humedad, registro.viento,
                 registro.codigo_clima, registro.medido_en.isoformat(timespec="minutes"),
                 obtenido_en.isoformat(timespec="seconds")))

    def obtener(self, consulta):
        """Plan B: devuelve (registro, obtenido_en) o (None, None) si nunca se consultó."""
        with obtener_conexion() as conn:
            fila = conn.execute("""SELECT ciudad, region, latitud, longitud, temperatura,
                humedad, viento, codigo_clima, medido_en, obtenido_en
                FROM clima WHERE consulta = ?""", (consulta.lower(),)).fetchone()
        if fila is None:
            return None, None
        try:
            registro = RegistroClima(fila[0], fila[1], fila[2], fila[3], fila[4], fila[5],
                                     fila[6], fila[7], datetime.fromisoformat(fila[8]))
        except ValueError as error:
            log.warning("El clima guardado de %r no es válido: %s", consulta, error)
            return None, None
        return registro, datetime.fromisoformat(fila[9])
