import logging

from dominio.validador_entradas import ValidadorEntradas
from servicios.cliente_http import ClienteHTTP

URL_TIPO_CAMBIO = "https://open.er-api.com/v6/latest"


class ClienteTipoCambio(ClienteHTTP):
    """Consume la API pública de ExchangeRate-API (open.er-api.com)."""

    NOMBRE = "ExchangeRate-API"
    _log = logging.getLogger(__name__)

    def __init__(self, url=URL_TIPO_CAMBIO, **opciones):
        """La URL se puede cambiar en las pruebas, pero siempre debe ser https."""
        self._url = self.exigir_https(url, "La URL del tipo de cambio debe comenzar con https://.")
        super().__init__(**opciones)

    def obtener_tasas(self, moneda_base="USD"):
        """La moneda va en la ruta, por eso se valida con una lista blanca de formato."""
        moneda_base = ValidadorEntradas.codigo_moneda(moneda_base)
        return self._solicitar("GET", f"{self._url}/{moneda_base}",
                               encabezados={"Accept": "application/json"})
