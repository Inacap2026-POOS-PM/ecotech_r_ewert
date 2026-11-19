import hashlib
import hmac
import logging

import bcrypt

from seguridad.cifrado import cifrar

registro = logging.getLogger(__name__)

RONDAS_BCRYPT = 12
MAXIMO_BYTES_BCRYPT = 72


def hashear_password(password_plano, sal=None):
    """Genera un hash bcrypt con sal aleatoria; la sal queda incluida en el resultado."""
    if sal is None:
        sal = bcrypt.gensalt(rounds=RONDAS_BCRYPT)
    hash_bytes = bcrypt.hashpw(password_plano.encode("utf-8"), sal)
    return sal.decode("ascii"), hash_bytes.decode("ascii")


def verificar_password(intento, hash_almacenado):
    """Compara el intento con el hash guardado sin conocer la contraseña original."""
    try:
        return bcrypt.checkpw(intento.encode("utf-8"), hash_almacenado.encode("ascii"))
    except ValueError:
        return False


def _es_bcrypt(hash_almacenado):
    """Los hashes bcrypt comienzan con $2a$, $2b$ o $2y$."""
    return hash_almacenado[:4] in ("$2a$", "$2b$", "$2y$")


def _verificar_pbkdf2(intento, sal_hex, hash_hex):
    """Valida las cuentas creadas en la E2 para migrarlas a bcrypt."""
    calculado = hashlib.pbkdf2_hmac("sha256", intento.encode("utf-8"),
                                    bytes.fromhex(sal_hex), 600000)
    return hmac.compare_digest(calculado.hex(), hash_hex)


class GestorAutenticacion:
    """Valida el acceso y los permisos de la sesión de consola."""

    def __init__(self, repositorio):
        """Recibe el repositorio para no mezclar autenticación con SQL."""
        self.repositorio = repositorio
        self.usuario_actual = None

    def registrar_usuario(self, nombre, contrasenia, rol):
        """El primer usuario es administrador; después solo un admin crea cuentas."""
        if not isinstance(nombre, str) or not nombre.strip():
            raise ValueError("El nombre de usuario es obligatorio.")
        nombre = nombre.strip()
        if len(nombre) > 50:
            raise ValueError("El nombre de usuario admite hasta 50 caracteres.")
        if not isinstance(contrasenia, str) or len(contrasenia) < 10:
            raise ValueError("La contraseña debe tener al menos 10 caracteres.")
        if len(contrasenia.encode("utf-8")) > MAXIMO_BYTES_BCRYPT:
            raise ValueError("La contraseña admite hasta 72 bytes.")
        if rol not in ("admin", "consulta"):
            raise ValueError("El rol debe ser admin o consulta.")
        if self.repositorio.hay_usuarios():
            if not self.verificar_rol(self.usuario_actual, "usuarios"):
                raise ValueError("Solo un administrador puede crear usuarios.")
        elif rol != "admin":
            raise ValueError("El primer usuario debe ser administrador.")
        sal, hash_clave = hashear_password(contrasenia)
        self.repositorio.guardar(nombre, sal, hash_clave, rol)

    def iniciar_sesion(self, usuario, contrasenia):
        """Compara hashes y borra la sesión anterior si falla el acceso."""
        self.usuario_actual = None
        if not isinstance(usuario, str) or not isinstance(contrasenia, str):
            return False
        if len(contrasenia.encode("utf-8")) > MAXIMO_BYTES_BCRYPT:
            return False
        datos = self.repositorio.obtener(usuario.strip())
        if datos is None:
            return False
        nombre, sal, hash_clave = datos[0], datos[1], datos[2]
        if _es_bcrypt(hash_clave):
            if not verificar_password(contrasenia, hash_clave):
                return False
        else:
            if not _verificar_pbkdf2(contrasenia, sal, hash_clave):
                return False
            nueva_sal, nuevo_hash = hashear_password(contrasenia)
            self.repositorio.actualizar_hash(nombre, nueva_sal, nuevo_hash)
            registro.info("La cuenta %s se migró de PBKDF2 a bcrypt.", nombre)
        self.usuario_actual = nombre
        return True

    def verificar_rol(self, usuario, modulo):
        """Revisa los permisos de quien inició sesión, no de otro nombre recibido."""
        if usuario is None or usuario != self.usuario_actual:
            return False
        datos = self.repositorio.obtener(usuario)
        if datos is None:
            return False
        if modulo == "consulta":
            return datos[3] in ("admin", "consulta")
        if modulo in ("administrar", "usuarios"):
            return datos[3] == "admin"
        return False

    def cifrar_datos_sensibles(self, datos):
        """Cifra datos que sí se deben volver a leer, como los contactos."""
        return cifrar(datos)
