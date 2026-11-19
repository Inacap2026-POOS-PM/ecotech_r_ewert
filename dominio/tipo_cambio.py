import math
from datetime import datetime

from dominio.validador_entradas import ValidadorEntradas


class TipoCambio:
    """Cuántas unidades de la moneda destino vale una unidad de la moneda base."""

    def __init__(self, moneda_base, moneda_destino, valor, actualizado_en):
        self.moneda_base = moneda_base
        self.moneda_destino = moneda_destino
        self.valor = valor
        self.actualizado_en = actualizado_en

    @property
    def moneda_base(self):
        return self._moneda_base

    @moneda_base.setter
    def moneda_base(self, codigo):
        self._moneda_base = ValidadorEntradas.codigo_moneda(codigo)

    @property
    def moneda_destino(self):
        return self._moneda_destino

    @moneda_destino.setter
    def moneda_destino(self, codigo):
        self._moneda_destino = ValidadorEntradas.codigo_moneda(codigo)

    @property
    def valor(self):
        return self._valor

    @valor.setter
    def valor(self, valor):
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            raise ValueError("El tipo de cambio debe ser un número.")
        if not math.isfinite(valor) or valor <= 0:
            raise ValueError("El tipo de cambio debe ser un número positivo.")
        self._valor = float(valor)

    @property
    def actualizado_en(self):
        return self._actualizado_en

    @actualizado_en.setter
    def actualizado_en(self, fecha):
        if not isinstance(fecha, datetime):
            raise ValueError("La fecha de actualización debe ser un datetime.")
        self._actualizado_en = fecha

    def convertir(self, monto):
        """Convierte un monto de la moneda base a la moneda destino."""
        return ValidadorEntradas.numero(monto, "El monto", estricto=True) * self.valor

    def resumen(self):
        """Una línea lista para mostrar en consola."""
        fecha = self.actualizado_en.astimezone().strftime("%d-%m-%Y %H:%M")
        return (f"1 {self.moneda_base} = {self.valor:,.2f} {self.moneda_destino} "
                f"(actualizado por la fuente el {fecha})")
