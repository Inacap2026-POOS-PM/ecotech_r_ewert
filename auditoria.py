"""Audita las cinco señales de riesgo del código asistido por IA.

Uso: python auditoria.py           (incluye la consulta a PyPI)
     python auditoria.py --sin-red (solo el análisis del código)
"""

import ast
import re
import sys
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent
EXCLUIDAS = {".venv", "venv", "__pycache__", ".git"}
PALABRAS_SQL = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER)\b", re.IGNORECASE)
METODOS_HTTP = {"get", "post", "put", "patch", "delete", "head", "options", "request"}
CLIENTES_HTTP = re.compile(r"^(requests|_?sesion|_?session|http)$")
SENALES = {
    "sql": "SQL armado con f-strings, % o .format",
    "tls": "verify=False desactiva TLS",
    "except": "except que oculta el error con pass",
    "timeout": "petición HTTP sin timeout",
    "pypi": "librería que no existe en PyPI",
}


def _nombre(nodo):
    """Nombre del objeto que recibe la llamada, por ejemplo requests o self._sesion."""
    if isinstance(nodo, ast.Name):
        return nodo.id
    if isinstance(nodo, ast.Attribute):
        return nodo.attr
    return ""


def _es_sql_dinamico(nodo):
    """Detecta texto SQL construido con datos en vez de parámetros."""
    if isinstance(nodo, ast.JoinedStr):
        texto = "".join(parte.value for parte in nodo.values
                        if isinstance(parte, ast.Constant) and isinstance(parte.value, str))
        return bool(PALABRAS_SQL.search(texto)) and any(
            isinstance(parte, ast.FormattedValue) for parte in nodo.values)
    if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.Mod):
        return (isinstance(nodo.left, ast.Constant) and isinstance(nodo.left.value, str)
                and bool(PALABRAS_SQL.search(nodo.left.value)))
    if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute):
        base = nodo.func.value
        return (nodo.func.attr == "format" and isinstance(base, ast.Constant)
                and isinstance(base.value, str) and bool(PALABRAS_SQL.search(base.value)))
    return False


def _oculta_error(manejador):
    """except Exception (o except sin tipo) cuyo cuerpo no hace nada."""
    amplio = manejador.type is None or (isinstance(manejador.type, ast.Name)
                                        and manejador.type.id in ("Exception", "BaseException"))
    vacio = all(isinstance(linea, (ast.Pass, ast.Continue)) or (
        isinstance(linea, ast.Expr) and isinstance(linea.value, ast.Constant))
        for linea in manejador.body)
    return amplio and vacio


def auditar_codigo(texto, archivo="<código>"):
    """Devuelve una lista de (señal, archivo, línea, detalle)."""
    hallazgos = []
    for nodo in ast.walk(ast.parse(texto, filename=archivo)):
        if isinstance(nodo, ast.Call):
            for argumento in nodo.args[:1]:
                if _es_sql_dinamico(argumento):
                    hallazgos.append(("sql", archivo, nodo.lineno, "consulta armada con datos"))
            for clave in nodo.keywords:
                if (clave.arg == "verify" and isinstance(clave.value, ast.Constant)
                        and clave.value.value is False):
                    hallazgos.append(("tls", archivo, nodo.lineno, "verify=False"))
            if (isinstance(nodo.func, ast.Attribute) and nodo.func.attr in METODOS_HTTP
                    and CLIENTES_HTTP.match(_nombre(nodo.func.value))
                    and not any(clave.arg == "timeout" for clave in nodo.keywords)):
                hallazgos.append(("timeout", archivo, nodo.lineno,
                                  f"{_nombre(nodo.func.value)}.{nodo.func.attr}() sin timeout"))
        elif isinstance(nodo, (ast.Assign, ast.AnnAssign)):
            valor = nodo.value
            objetivos = nodo.targets if isinstance(nodo, ast.Assign) else [nodo.target]
            if any(isinstance(o, ast.Attribute) and o.attr == "verify" for o in objetivos) and (
                    isinstance(valor, ast.Constant) and valor.value is False):
                hallazgos.append(("tls", archivo, nodo.lineno, "sesion.verify = False"))
            if valor is not None and _es_sql_dinamico(valor):
                hallazgos.append(("sql", archivo, nodo.lineno, "SQL guardado en una variable"))
        elif isinstance(nodo, ast.ExceptHandler) and _oculta_error(nodo):
            hallazgos.append(("except", archivo, nodo.lineno, "el error se descarta"))
    return hallazgos


def archivos_python(raiz=RAIZ):
    """Recorre el proyecto sin entrar al entorno virtual."""
    for ruta in sorted(raiz.rglob("*.py")):
        if not EXCLUIDAS.intersection(ruta.relative_to(raiz).parts):
            yield ruta


def auditar_proyecto(raiz=RAIZ):
    """Aplica las cuatro señales de código a cada archivo del proyecto."""
    hallazgos = []
    for ruta in archivos_python(raiz):
        relativa = ruta.relative_to(raiz).as_posix()
        hallazgos.extend(auditar_codigo(ruta.read_text(encoding="utf-8"), relativa))
    return hallazgos


def leer_requirements(ruta=RAIZ / "requirements.txt"):
    """Devuelve pares (paquete, versión) de las líneas con ==."""
    paquetes = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.split("#", 1)[0].strip()
        if linea:
            nombre, _, version = linea.partition("==")
            paquetes.append((nombre.strip(), version.strip()))
    return paquetes


def verificar_pypi(paquetes, sesion=None):
    """Consulta PyPI por cada paquete y versión; un fallo de red queda como no verificado."""
    sesion = sesion or requests.Session()
    resultados = []
    for nombre, version in paquetes:
        ruta = f"https://pypi.org/pypi/{nombre}/{version}/json" if version else \
            f"https://pypi.org/pypi/{nombre}/json"
        try:
            respuesta = sesion.get(ruta, timeout=(3, 10))
        except requests.exceptions.RequestException as error:
            resultados.append((nombre, version, "no verificado", type(error).__name__))
            continue
        if respuesta.status_code == 200:
            resultados.append((nombre, version, "existe", ""))
        elif respuesta.status_code == 404:
            resultados.append((nombre, version, "no existe", "404 en PyPI"))
        else:
            resultados.append((nombre, version, "no verificado", str(respuesta.status_code)))
    return resultados


def main(argumentos):
    """Imprime el informe y termina con código 1 si hay hallazgos."""
    hallazgos = auditar_proyecto()
    print("Auditoría de código asistido por IA\n")
    for clave in ("sql", "tls", "except", "timeout"):
        propios = [h for h in hallazgos if h[0] == clave]
        print(f"[{'OK' if not propios else 'REVISAR'}] {SENALES[clave]}: {len(propios)}")
        for _, archivo, linea, detalle in propios:
            print(f"    {archivo}:{linea} {detalle}")
    problemas = len(hallazgos)
    if "--sin-red" in argumentos:
        print("[--] Librerías en PyPI: no consultado (--sin-red)")
    else:
        resultados = verificar_pypi(leer_requirements())
        faltantes = [r for r in resultados if r[2] != "existe"]
        print(f"[{'OK' if not faltantes else 'REVISAR'}] {SENALES['pypi']}: {len(faltantes)}")
        for nombre, version, estado, detalle in resultados:
            print(f"    {nombre}=={version}: {estado} {detalle}".rstrip())
        problemas += len(faltantes)
    return 1 if problemas else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
