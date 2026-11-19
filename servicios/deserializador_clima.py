from datetime import datetime

from dominio.registro_clima import RegistroClima
from servicios.excepciones import RespuestaInvalida


class CiudadNoEncontrada(ValueError):
    """Open-Meteo respondió bien, pero no conoce esa ciudad en Chile."""


class DeserializadorClima:
    """Convierte el JSON de Open-Meteo en un RegistroClima validado y tipado."""

    def ubicacion(self, datos, ciudad):
        """Lee el primer resultado de la geocodificación con .get() y revisa sus tipos."""
        if not isinstance(datos, dict):
            raise RespuestaInvalida("La geocodificación no devolvió un objeto JSON.")
        resultados = datos.get("results")
        if resultados is None or resultados == []:
            raise CiudadNoEncontrada(f"No se encontró la ciudad «{ciudad}» en Chile.")
        if not isinstance(resultados, list) or not isinstance(resultados[0], dict):
            raise RespuestaInvalida("La geocodificación tiene una estructura inesperada.")
        primero = resultados[0]
        nombre = primero.get("name")
        region = primero.get("admin1", "")
        latitud = primero.get("latitude")
        longitud = primero.get("longitude")
        if not isinstance(nombre, str) or not isinstance(region, str):
            raise RespuestaInvalida("La geocodificación entregó un nombre que no es texto.")
        for valor in (latitud, longitud):
            if isinstance(valor, bool) or not isinstance(valor, (int, float)):
                raise RespuestaInvalida("La geocodificación entregó coordenadas inválidas.")
        return {"ciudad": nombre, "region": region, "latitud": latitud, "longitud": longitud}

    def clima(self, datos, ubicacion):
        """Arma el objeto del dominio; si un dato no cumple sus reglas, la respuesta se rechaza."""
        if not isinstance(datos, dict) or not isinstance(datos.get("current"), dict):
            raise RespuestaInvalida("Open-Meteo no entregó el clima actual.")
        actual = datos["current"]
        hora = actual.get("time")
        try:
            medido_en = datetime.strptime(hora, "%Y-%m-%dT%H:%M")
        except (TypeError, ValueError) as error:
            raise RespuestaInvalida("La hora de medición no tiene el formato esperado.") from error
        try:
            return RegistroClima(ubicacion["ciudad"], ubicacion["region"], ubicacion["latitud"],
                                 ubicacion["longitud"], actual.get("temperature_2m"),
                                 actual.get("relative_humidity_2m"), actual.get("wind_speed_10m"),
                                 actual.get("weather_code"), medido_en)
        except ValueError as error:
            raise RespuestaInvalida(f"Open-Meteo entregó datos inválidos: {error}") from error
