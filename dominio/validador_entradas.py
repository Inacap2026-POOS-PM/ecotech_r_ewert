import math
import re
from datetime import date


class ValidadorEntradas:
    """Revisa los datos antes de modificar un objeto."""

    @staticmethod
    def sanitizar_texto(entrada):
        """Quita espacios exteriores; el SQL se protege con parámetros, no aquí."""
        if not isinstance(entrada, str):
            raise ValueError("Se esperaba un texto.")
        return entrada.strip()

    @staticmethod
    def texto(valor, campo):
        """Exige un texto no vacío."""
        valor = ValidadorEntradas.sanitizar_texto(valor)
        if not valor:
            raise ValueError(f"{campo} no puede estar vacío.")
        return valor

    @staticmethod
    def numero(valor, campo, minimo=0, estricto=False):
        """Rechaza negativos, booleanos, infinitos y valores que no son números."""
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            raise ValueError(f"{campo} debe ser un número.")
        if not math.isfinite(valor) or valor < minimo or (estricto and valor == minimo):
            raise ValueError(f"{campo} está fuera del rango permitido.")
        return valor

    @staticmethod
    def fecha(valor):
        """Las clases usan date; la persistencia convierte desde y hacia ISO."""
        if type(valor) is not date:
            raise ValueError("La fecha debe ser un objeto date.")
        return valor

    @staticmethod
    def validar_email(email):
        """Valida la estructura básica de un correo, sin comprobar su existencia."""
        return isinstance(email, str) and bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email))

    @staticmethod
    def validar_telefono(telefono):
        """Acepta entre 8 y 15 dígitos y un prefijo + opcional."""
        return isinstance(telefono, str) and bool(re.fullmatch(r"\+?[0-9]{8,15}", telefono))

    @staticmethod
    def digito_verificador(cuerpo):
        """Calcula el dígito del RUT con el algoritmo módulo 11."""
        suma = 0
        factor = 2
        for digito in reversed(cuerpo):
            suma += int(digito) * factor
            factor = 2 if factor == 7 else factor + 1
        resto = 11 - suma % 11
        if resto == 11:
            return "0"
        if resto == 10:
            return "K"
        return str(resto)

    @staticmethod
    def codigo_moneda(valor):
        """Código ISO 4217 de tres letras; va en la ruta de la API, así que se valida antes."""
        valor = ValidadorEntradas.texto(valor, "La moneda").upper()
        if not re.fullmatch(r"[A-Z]{3}", valor):
            raise ValueError("La moneda debe ser un código de tres letras, como USD o CLP.")
        return valor

    @staticmethod
    def monto(valor, maximo=1_000_000_000):
        """Convierte el monto escrito por el usuario; acepta punto o coma como decimal."""
        valor = ValidadorEntradas.texto(valor, "El monto")
        if not re.fullmatch(r"[0-9]{1,10}([.,][0-9]{1,2})?", valor):
            raise ValueError("El monto debe ser un número positivo con hasta 2 decimales, "
                             "por ejemplo 150 o 99,90.")
        numero = float(valor.replace(",", "."))
        if numero <= 0 or numero > maximo:
            raise ValueError(f"El monto debe ser mayor que 0 y menor o igual a {maximo:,}.")
        return numero

    @staticmethod
    def rut(valor):
        """Acepta el RUT con o sin puntos y comprueba su dígito verificador."""
        valor = ValidadorEntradas.texto(valor, "RUT").upper().replace(".", "")
        if not re.fullmatch(r"[0-9]{7,8}-[0-9K]", valor):
            raise ValueError("El RUT debe tener un formato como 12345678-5.")
        cuerpo, digito = valor.split("-")
        if ValidadorEntradas.digito_verificador(cuerpo) != digito:
            raise ValueError("El dígito verificador del RUT no es válido.")
        return valor
