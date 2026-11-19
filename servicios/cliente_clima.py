import logging

from servicios.cliente_http import ClienteHTTP

URL_GEOCODIFICACION = "https://geocoding-api.open-meteo.com/v1/search"
URL_PRONOSTICO = "https://api.open-meteo.com/v1/forecast"
VARIABLES_ACTUALES = "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code"


class ClienteClima(ClienteHTTP):
    """Consume Open-Meteo: primero ubica la ciudad y luego pide su clima actual."""

    NOMBRE = "Open-Meteo"
    _log = logging.getLogger(__name__)

    def __init__(self, url_geocodificacion=URL_GEOCODIFICACION, url_pronostico=URL_PRONOSTICO,
                 **opciones):
        """Las URL se pueden cambiar en las pruebas, pero siempre deben ser https."""
        self._url_geocodificacion = self.exigir_https(
            url_geocodificacion, "La URL de geocodificación debe comenzar con https://.")
        self._url_pronostico = self.exigir_https(
            url_pronostico, "La URL del pronóstico debe comenzar con https://.")
        super().__init__(**opciones)

    def buscar_ciudad(self, ciudad):
        """La ciudad ya validada viaja en params: requests la codifica, no se concatena."""
        return self._solicitar("GET", self._url_geocodificacion,
                               params={"name": ciudad, "count": 1, "language": "es",
                                       "country_code": "CL", "format": "json"},
                               encabezados={"Accept": "application/json"})

    def obtener_clima_actual(self, latitud, longitud):
        """Pide temperatura, humedad, viento y código de clima de la ubicación."""
        return self._solicitar("GET", self._url_pronostico,
                               params={"latitude": latitud, "longitude": longitud,
                                       "current": VARIABLES_ACTUALES,
                                       "timezone": "America/Santiago"},
                               encabezados={"Accept": "application/json"})
