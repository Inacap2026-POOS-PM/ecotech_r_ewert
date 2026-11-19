import re
from dataclasses import dataclass, field
from datetime import date

from dominio.empleado_contratista import EmpleadoContratista
from dominio.empleado_planta import EmpleadoPlanta
from servicios.excepciones import RespuestaInvalida


@dataclass(frozen=True)
class Rechazo:
    """Fila externa que no se guardó y el motivo."""
    posicion: int
    rut: str
    motivo: str


@dataclass
class ResultadoDeserializacion:
    """Separa los empleados válidos de las filas rechazadas."""
    empleados: list = field(default_factory=list)
    rechazados: list = field(default_factory=list)


class DeserializadorEmpleados:
    """Convierte el JSON de Supabase en empleados del dominio, validados y tipados."""

    MAXIMO_REGISTROS = 1000
    LARGOS = {"rut": 12, "nombre": 100, "tipo_contrato": 20, "direccion": 200,
              "telefono": 16, "email": 254}

    def deserializar(self, datos):
        """Una fila inválida se rechaza sin detener la carga de las demás."""
        if not isinstance(datos, list):
            raise RespuestaInvalida("Se esperaba una lista de empleados.")
        if len(datos) > self.MAXIMO_REGISTROS:
            raise RespuestaInvalida(
                f"La respuesta supera el máximo de {self.MAXIMO_REGISTROS} empleados.")
        resultado = ResultadoDeserializacion()
        vistos = set()
        for posicion, registro in enumerate(datos, start=1):
            try:
                empleado = self.convertir(registro)
                if empleado.rut in vistos:
                    raise ValueError("El RUT aparece repetido en la respuesta.")
            except ValueError as error:
                resultado.rechazados.append(
                    Rechazo(posicion, self._rut_informado(registro), str(error)))
                continue
            vistos.add(empleado.rut)
            resultado.empleados.append(empleado)
        return resultado

    def convertir(self, registro):
        """Revisa tipos y largos; las clases del dominio validan formato y rangos."""
        if not isinstance(registro, dict):
            raise ValueError("La fila no es un objeto JSON.")
        rut = self._texto(registro, "rut", obligatorio=True)
        nombre = self._texto(registro, "nombre", obligatorio=True)
        fecha_ingreso = self._fecha(registro, "fecha_ingreso")
        sueldo = self._numero(registro, "sueldo_base")
        tipo = self._texto(registro, "tipo_contrato", obligatorio=True).lower()
        direccion = self._texto(registro, "direccion")
        telefono = self._texto(registro, "telefono")
        email = self._texto(registro, "email")
        if tipo == "planta":
            bono = self._numero(registro, "bono", defecto=0)
            return EmpleadoPlanta(rut, nombre, fecha_ingreso, sueldo, bono,
                                  direccion, telefono, email)
        if tipo == "contratista":
            tarifa = self._numero(registro, "tarifa_hora")
            return EmpleadoContratista(rut, nombre, fecha_ingreso, sueldo, tarifa,
                                       direccion, telefono, email)
        raise ValueError("tipo_contrato debe ser planta o contratista.")

    def _texto(self, registro, campo, obligatorio=False):
        """Usa .get() para que una llave faltante o null no provoque KeyError."""
        valor = registro.get(campo)
        if valor is None:
            if obligatorio:
                raise ValueError(f"Falta el campo {campo}.")
            return ""
        if not isinstance(valor, str):
            raise ValueError(f"{campo} debe ser texto.")
        valor = valor.strip()
        if len(valor) > self.LARGOS[campo]:
            raise ValueError(f"{campo} supera los {self.LARGOS[campo]} caracteres.")
        return valor

    @staticmethod
    def _numero(registro, campo, defecto=None):
        """Rechaza textos, booleanos y nulos donde se espera un número."""
        valor = registro.get(campo)
        if valor is None:
            if defecto is None:
                raise ValueError(f"Falta el campo {campo}.")
            return defecto
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            raise ValueError(f"{campo} debe ser un número.")
        return valor

    @staticmethod
    def _fecha(registro, campo):
        """Acepta solo AAAA-MM-DD y no admite fechas futuras."""
        valor = registro.get(campo)
        if not isinstance(valor, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
            raise ValueError(f"{campo} debe tener el formato AAAA-MM-DD.")
        try:
            fecha = date.fromisoformat(valor)
        except ValueError as error:
            raise ValueError(f"{campo} no es una fecha real.") from error
        if fecha > date.today():
            raise ValueError(f"{campo} no puede ser una fecha futura.")
        return fecha

    @staticmethod
    def _rut_informado(registro):
        """Identifica la fila rechazada sin mostrar textos largos o maliciosos completos."""
        if isinstance(registro, dict) and isinstance(registro.get("rut"), str):
            return registro["rut"][:12]
        return ""
