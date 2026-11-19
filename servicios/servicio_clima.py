import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from dominio.registro_clima import RegistroClima
from dominio.validador_entradas import ValidadorEntradas
from infraestructura.clima_repositorio import ClimaRepositorio
from servicios.cliente_clima import ClienteClima
from servicios.deserializador_clima import DeserializadorClima
from servicios.excepciones import ErrorServicioExterno
from servicios.sincronizador import API, LOCAL, SIN_DATOS

log = logging.getLogger(__name__)


@dataclass
class ResultadoClima:
    """Indica de dónde viene el clima para que la consola lo explique."""
    origen: str
    registro: RegistroClima = None
    obtenido_en: datetime = None
    motivo: str = ""


class ServicioClima:
    """Lo ideal es Open-Meteo; el plan B, el último clima guardado; el plan C, un aviso."""

    def __init__(self, crear_cliente=ClienteClima, deserializador=None, repositorio=None,
                 reloj=None):
        """Las dependencias se pueden reemplazar en las pruebas."""
        self._crear_cliente = crear_cliente
        self._deserializador = deserializador or DeserializadorClima()
        self._repositorio = repositorio or ClimaRepositorio()
        self._reloj = reloj or (lambda: datetime.now(timezone.utc))

    def consultar(self, ciudad):
        """Valida la entrada antes de llamar a la API; si la API falla, usa los datos locales."""
        consulta = ValidadorEntradas.ciudad(ciudad)
        try:
            cliente = self._crear_cliente()
            ubicacion = self._deserializador.ubicacion(cliente.buscar_ciudad(consulta), consulta)
            datos = cliente.obtener_clima_actual(ubicacion["latitud"], ubicacion["longitud"])
            registro = self._deserializador.clima(datos, ubicacion)
        except ErrorServicioExterno as error:
            log.error("No se pudo usar Open-Meteo: %s", error)
            return self._plan_b(consulta, str(error))
        obtenido_en = self._reloj()
        self._repositorio.guardar(consulta, registro, obtenido_en)
        log.info("Clima de %s guardado.", registro.ciudad)
        return ResultadoClima(API, registro, obtenido_en)

    def _plan_b(self, consulta, motivo):
        """Muestra el último clima guardado de esa ciudad o avisa que no hay datos."""
        registro, obtenido_en = self._repositorio.obtener(consulta)
        if registro is None:
            return ResultadoClima(SIN_DATOS, motivo=motivo)
        return ResultadoClima(LOCAL, registro, obtenido_en, motivo)
