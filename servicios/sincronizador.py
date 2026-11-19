import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from infraestructura.configuracion import ErrorDeConfiguracion
from infraestructura.empleado_repositorio import EmpleadoRepositorio
from servicios.cliente_supabase import ClienteSupabase
from servicios.deserializador_empleados import DeserializadorEmpleados, Rechazo
from servicios.excepciones import ErrorServicioExterno

log = logging.getLogger(__name__)

API = "api"
LOCAL = "local"
SIN_DATOS = "sin_datos"


@dataclass
class ResultadoSincronizacion:
    """Indica de dónde vienen los datos para que la consola lo informe con claridad."""
    origen: str
    empleados: list = field(default_factory=list)
    rechazados: list = field(default_factory=list)
    obtenido_en: datetime = None
    motivo: str = ""


def describir_antiguedad(obtenido_en, ahora=None):
    """Convierte la fecha de obtención en un texto como 'hace 2 h 5 min'."""
    ahora = ahora or datetime.now(timezone.utc)
    minutos = max(0, int((ahora - obtenido_en).total_seconds() // 60))
    dias, minutos = divmod(minutos, 1440)
    horas, minutos = divmod(minutos, 60)
    if dias:
        return f"hace {dias} d {horas} h"
    if horas:
        return f"hace {horas} h {minutos} min"
    return f"hace {minutos} min"


class SincronizadorEmpleados:
    """Lo ideal es Supabase; el plan B, los datos locales; el plan C, un aviso claro."""

    def __init__(self, crear_cliente=ClienteSupabase.desde_entorno, deserializador=None,
                 repositorio=None, reloj=None):
        """Las dependencias se pueden reemplazar en las pruebas."""
        self._crear_cliente = crear_cliente
        self._deserializador = deserializador or DeserializadorEmpleados()
        self._repositorio = repositorio or EmpleadoRepositorio()
        self._reloj = reloj or (lambda: datetime.now(timezone.utc))

    def sincronizar(self):
        """Consulta, valida y guarda; si el servicio falla, usa los datos locales."""
        try:
            datos = self._crear_cliente().obtener_empleados()
            resultado = self._deserializador.deserializar(datos)
        except (ErrorDeConfiguracion, ErrorServicioExterno) as error:
            log.error("No se pudo usar Supabase: %s", error)
            return self._plan_b(str(error))
        obtenido_en = self._reloj()
        guardados = []
        rechazados = list(resultado.rechazados)
        for empleado in resultado.empleados:
            try:
                self._repositorio.sincronizar(empleado, obtenido_en)
            except ValueError as error:
                rechazados.append(Rechazo(0, empleado.rut, str(error)))
                continue
            guardados.append(empleado)
        for rechazo in rechazados:
            log.warning("Fila %s (RUT %r) rechazada: %s",
                        rechazo.posicion or "-", rechazo.rut, rechazo.motivo)
        log.info("Sincronización: %d guardados y %d rechazados.", len(guardados), len(rechazados))
        return ResultadoSincronizacion(API, guardados, rechazados, obtenido_en)

    def _plan_b(self, motivo):
        """Muestra lo último guardado e indica su antigüedad; si no hay nada, lo dice."""
        empleados, obtenido_en = self._repositorio.listar_sincronizados()
        if not empleados:
            return ResultadoSincronizacion(SIN_DATOS, motivo=motivo)
        return ResultadoSincronizacion(LOCAL, empleados, [], obtenido_en, motivo)
