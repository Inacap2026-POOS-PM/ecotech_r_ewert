class ErrorServicioExterno(Exception):
    """Base de los fallos al consumir un servicio externo."""


class ServicioNoDisponible(ErrorServicioExterno):
    """Sin red, sin respuesta, error 5xx o 429 después de agotar los reintentos."""


class ErrorCertificado(ErrorServicioExterno):
    """El certificado TLS no es válido; nunca se desactiva la verificación."""


class ErrorAutenticacion(ErrorServicioExterno):
    """Credenciales rechazadas o token inválido incluso después de renovarlo (401)."""


class ErrorPermiso(ErrorServicioExterno):
    """El usuario está identificado, pero no tiene permiso (403)."""


class RecursoNoEncontrado(ErrorServicioExterno):
    """La ruta o la tabla pedida no existe (404)."""


class PeticionRechazada(ErrorServicioExterno):
    """Otro error 4xx: el problema está en la petición y no se reintenta."""


class RespuestaInvalida(ErrorServicioExterno):
    """La respuesta no es JSON o no tiene la estructura esperada."""
