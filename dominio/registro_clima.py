import math
from datetime import datetime

from dominio.validador_entradas import ValidadorEntradas

# Códigos WMO que entrega Open-Meteo en weather_code.
DESCRIPCIONES_WMO = {
    0: "Despejado", 1: "Mayormente despejado", 2: "Parcialmente nublado", 3: "Nublado",
    45: "Niebla", 48: "Niebla con escarcha",
    51: "Llovizna débil", 53: "Llovizna", 55: "Llovizna intensa",
    56: "Llovizna helada", 57: "Llovizna helada intensa",
    61: "Lluvia débil", 63: "Lluvia", 65: "Lluvia intensa",
    66: "Lluvia helada", 67: "Lluvia helada intensa",
    71: "Nieve débil", 73: "Nieve", 75: "Nieve intensa", 77: "Granizo fino",
    80: "Chubascos débiles", 81: "Chubascos", 82: "Chubascos violentos",
    85: "Chubascos de nieve", 86: "Chubascos de nieve intensos",
    95: "Tormenta", 96: "Tormenta con granizo", 99: "Tormenta con granizo intenso",
}


class RegistroClima:
    """Clima actual de una ciudad; cada dato se valida antes de asignarse."""

    def __init__(self, ciudad, region, latitud, longitud, temperatura, humedad, viento,
                 codigo_clima, medido_en):
        self.ciudad = ciudad
        self.region = region
        self.latitud = latitud
        self.longitud = longitud
        self.temperatura = temperatura
        self.humedad = humedad
        self.viento = viento
        self.codigo_clima = codigo_clima
        self.medido_en = medido_en

    @staticmethod
    def _en_rango(valor, campo, minimo, maximo):
        """Acepta solo números reales finitos dentro del rango físico posible."""
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            raise ValueError(f"{campo} debe ser un número.")
        if not math.isfinite(valor) or not minimo <= valor <= maximo:
            raise ValueError(f"{campo} está fuera del rango permitido.")
        return float(valor)

    @property
    def ciudad(self):
        return self._ciudad

    @ciudad.setter
    def ciudad(self, valor):
        valor = ValidadorEntradas.texto(valor, "La ciudad")
        if len(valor) > 100:
            raise ValueError("La ciudad admite hasta 100 caracteres.")
        self._ciudad = valor

    @property
    def region(self):
        return self._region

    @region.setter
    def region(self, valor):
        valor = ValidadorEntradas.sanitizar_texto(valor or "")
        if len(valor) > 100:
            raise ValueError("La región admite hasta 100 caracteres.")
        self._region = valor

    @property
    def latitud(self):
        return self._latitud

    @latitud.setter
    def latitud(self, valor):
        self._latitud = self._en_rango(valor, "La latitud", -90, 90)

    @property
    def longitud(self):
        return self._longitud

    @longitud.setter
    def longitud(self, valor):
        self._longitud = self._en_rango(valor, "La longitud", -180, 180)

    @property
    def temperatura(self):
        return self._temperatura

    @temperatura.setter
    def temperatura(self, valor):
        self._temperatura = self._en_rango(valor, "La temperatura", -90, 60)

    @property
    def humedad(self):
        return self._humedad

    @humedad.setter
    def humedad(self, valor):
        self._humedad = self._en_rango(valor, "La humedad", 0, 100)

    @property
    def viento(self):
        return self._viento

    @viento.setter
    def viento(self, valor):
        self._viento = self._en_rango(valor, "El viento", 0, 500)

    @property
    def codigo_clima(self):
        return self._codigo_clima

    @codigo_clima.setter
    def codigo_clima(self, valor):
        if isinstance(valor, bool) or not isinstance(valor, int) or valor not in DESCRIPCIONES_WMO:
            raise ValueError("El código de clima no es un código WMO conocido.")
        self._codigo_clima = valor

    @property
    def medido_en(self):
        return self._medido_en

    @medido_en.setter
    def medido_en(self, valor):
        if not isinstance(valor, datetime):
            raise ValueError("La hora de medición debe ser un datetime.")
        self._medido_en = valor

    def descripcion(self):
        """Traduce el código WMO a un texto para el usuario."""
        return DESCRIPCIONES_WMO[self.codigo_clima]

    def resumen(self):
        """Una línea lista para mostrar en consola."""
        lugar = f"{self.ciudad} ({self.region})" if self.region else self.ciudad
        return (f"{lugar}: {self.temperatura:.1f} °C, {self.descripcion().lower()}, "
                f"humedad {self.humedad:.0f} %, viento {self.viento:.1f} km/h "
                f"(medido {self.medido_en:%d-%m-%Y %H:%M})")
