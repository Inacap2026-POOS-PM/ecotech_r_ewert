import logging
from datetime import datetime

from dominio.tipo_cambio import TipoCambio
from infraestructura.conexion import obtener_conexion

log = logging.getLogger(__name__)


class TipoCambioRepositorio:
    """Guarda el último tipo de cambio de cada par de monedas con consultas parametrizadas."""

    def guardar(self, tipo_cambio, obtenido_en):
        """Inserta o actualiza el par y registra cuándo se obtuvo el dato."""
        with obtener_conexion() as conn:
            conn.execute("""INSERT INTO tipo_cambio
                (moneda_base, moneda_destino, valor, actualizado_en, obtenido_en)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(moneda_base, moneda_destino) DO UPDATE SET
                valor = excluded.valor, actualizado_en = excluded.actualizado_en,
                obtenido_en = excluded.obtenido_en""",
                (tipo_cambio.moneda_base, tipo_cambio.moneda_destino, tipo_cambio.valor,
                 tipo_cambio.actualizado_en.isoformat(timespec="seconds"),
                 obtenido_en.isoformat(timespec="seconds")))

    def obtener(self, moneda_base, moneda_destino):
        """Plan B: devuelve (tipo_cambio, obtenido_en) o (None, None) si nunca se consultó."""
        with obtener_conexion() as conn:
            fila = conn.execute("""SELECT moneda_base, moneda_destino, valor, actualizado_en,
                obtenido_en FROM tipo_cambio WHERE moneda_base = ? AND moneda_destino = ?""",
                (moneda_base, moneda_destino)).fetchone()
        if fila is None:
            return None, None
        try:
            tipo_cambio = TipoCambio(fila[0], fila[1], fila[2], datetime.fromisoformat(fila[3]))
        except ValueError as error:
            log.warning("El tipo de cambio guardado %s/%s no es válido: %s",
                        moneda_base, moneda_destino, error)
            return None, None
        return tipo_cambio, datetime.fromisoformat(fila[4])
