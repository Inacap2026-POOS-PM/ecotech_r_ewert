import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from dominio.tipo_cambio import TipoCambio
from dominio.validador_entradas import ValidadorEntradas
from infraestructura.tipo_cambio_repositorio import TipoCambioRepositorio
from servicios.cliente_tipo_cambio import ClienteTipoCambio
from servicios.deserializador_tipo_cambio import DeserializadorTipoCambio
from servicios.excepciones import ErrorServicioExterno
from servicios.sincronizador import API, LOCAL, SIN_DATOS

log = logging.getLogger(__name__)

MONEDA_BASE = "USD"
MONEDA_DESTINO = "CLP"


@dataclass
class ResultadoTipoCambio:
    """Indica de dónde viene el tipo de cambio para que la consola lo explique."""
    origen: str
    monto: float
    tipo_cambio: TipoCambio = None
    obtenido_en: datetime = None
    motivo: str = ""

    def total(self):
        """Monto convertido a pesos, o None si no hay tipo de cambio."""
        return self.tipo_cambio.convertir(self.monto) if self.tipo_cambio else None


class ServicioTipoCambio:
    """Lo ideal es la API; el plan B, el último valor guardado; el plan C, un aviso."""

    def __init__(self, crear_cliente=ClienteTipoCambio, deserializador=None, repositorio=None,
                 reloj=None):
        """Las dependencias se pueden reemplazar en las pruebas."""
        self._crear_cliente = crear_cliente
        self._deserializador = deserializador or DeserializadorTipoCambio()
        self._repositorio = repositorio or TipoCambioRepositorio()
        self._reloj = reloj or (lambda: datetime.now(timezone.utc))

    def consultar(self, monto_escrito):
        """Valida el monto antes de llamar a la API; si la API falla, usa el valor local."""
        monto = ValidadorEntradas.monto(monto_escrito)
        try:
            datos = self._crear_cliente().obtener_tasas(MONEDA_BASE)
            tipo_cambio = self._deserializador.convertir(datos, MONEDA_BASE, MONEDA_DESTINO)
        except ErrorServicioExterno as error:
            log.error("No se pudo usar la API de tipo de cambio: %s", error)
            return self._plan_b(monto, str(error))
        obtenido_en = self._reloj()
        self._repositorio.guardar(tipo_cambio, obtenido_en)
        log.info("Tipo de cambio %s/%s guardado: %s", MONEDA_BASE, MONEDA_DESTINO,
                 tipo_cambio.valor)
        return ResultadoTipoCambio(API, monto, tipo_cambio, obtenido_en)

    def _plan_b(self, monto, motivo):
        """Usa el último tipo de cambio guardado o avisa que no hay datos."""
        tipo_cambio, obtenido_en = self._repositorio.obtener(MONEDA_BASE, MONEDA_DESTINO)
        if tipo_cambio is None:
            return ResultadoTipoCambio(SIN_DATOS, monto, motivo=motivo)
        return ResultadoTipoCambio(LOCAL, monto, tipo_cambio, obtenido_en, motivo)
