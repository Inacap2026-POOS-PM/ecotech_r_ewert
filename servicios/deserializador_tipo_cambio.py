from datetime import datetime, timezone

from dominio.tipo_cambio import TipoCambio
from servicios.excepciones import RespuestaInvalida


class DeserializadorTipoCambio:
    """Convierte el JSON de ExchangeRate-API en un TipoCambio validado y tipado."""

    def convertir(self, datos, moneda_base, moneda_destino):
        """La API puede responder 200 con "result": "error", así que no basta el código HTTP."""
        if not isinstance(datos, dict):
            raise RespuestaInvalida("La API de tipo de cambio no devolvió un objeto JSON.")
        if datos.get("result") != "success":
            tipo_error = datos.get("error-type", "desconocido")
            if not isinstance(tipo_error, str):
                tipo_error = "desconocido"
            raise RespuestaInvalida(f"La API de tipo de cambio informó un error: {tipo_error[:40]}.")
        if datos.get("base_code") != moneda_base:
            raise RespuestaInvalida("La API respondió con otra moneda base.")
        tasas = datos.get("rates")
        if not isinstance(tasas, dict):
            raise RespuestaInvalida("La respuesta no trae la lista de tasas.")
        if moneda_destino not in tasas:
            raise RespuestaInvalida(f"La respuesta no trae la tasa de {moneda_destino}.")
        segundos = datos.get("time_last_update_unix")
        if isinstance(segundos, bool) or not isinstance(segundos, int) or segundos <= 0:
            raise RespuestaInvalida("La fecha de actualización no es válida.")
        actualizado_en = datetime.fromtimestamp(segundos, timezone.utc)
        if actualizado_en > datetime.now(timezone.utc):
            raise RespuestaInvalida("La fecha de actualización está en el futuro.")
        try:
            return TipoCambio(moneda_base, moneda_destino, tasas.get(moneda_destino),
                              actualizado_en)
        except ValueError as error:
            raise RespuestaInvalida(f"La API entregó un tipo de cambio inválido: {error}") from error
