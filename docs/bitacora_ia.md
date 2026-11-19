# Bitácora de IA · UA3 (E3)

Registro del uso de IA en la Unidad 3: servicios externos y seguridad.
Herramienta: Claude Code (modelo Claude Opus 5.5) en VS Code, sobre la rama `rodolfo/ua3`.
Fechas de las sesiones: 18-11-2026 (entradas 1 a 13) y 19-11-2026 (entradas 14 a 16).

Cada entrada indica la petición, lo que propuso la IA y la decisión final
(**Acepto**, **Modifico** o **Descarto**) con su justificación técnica. Las
decisiones del estudiante se indican de forma explícita; el resto de los ajustes
se hicieron durante la sesión con IA y el estudiante los revisó antes de entregar.
Las pruebas citadas se ejecutan con `python -m unittest discover -s pruebas`.

## Peticiones y decisiones

| N° | Petición (prompt resumido) | Propuesta de la IA | Decisión | Justificación técnica |
| --- | --- | --- | --- | --- |
| 1 | Leer el repositorio completo y aplicar la UA3 según la retroalimentación del docente. | Diagnóstico: faltaban cliente HTTP, reintentos, deserializador, `obtenido_en`, plan B, bcrypt, dígito verificador y la auditoría. | Acepto | El diagnóstico coincide con el checklist de la retroalimentación y con la lista de pendientes del README. |
| 2 | ¿Qué API consumir? | Recomendó mindicador.cl, que es pública y no exige cuenta. | Descarto | El estudiante eligió Supabase: exige autenticación real con un Bearer JWT que caduca, así que el 401 y la renovación del token son casos reales y no simulados. |
| 3 | Cargar las credenciales de forma segura. | Módulo `infraestructura/configuracion.py` con `requerir()`, que informa todas las variables faltantes en un solo mensaje. | Modifico | Se agregó la exigencia de `https://` en la URL y se dejaron vacíos los valores de `.env.example`. Si alguien copia la plantilla sin completarla, recibe el mensaje claro en vez de conectarse a una URL de ejemplo. |
| 4 | Contraseñas con bcrypt o argon2. | Reemplazar PBKDF2 por `bcrypt.hashpw` con `gensalt(rounds=12)`. | Modifico | Se agregó la migración: una cuenta PBKDF2 de la E2 se valida una última vez y se vuelve a hashear con bcrypt al iniciar sesión. También se rechazan contraseñas de más de 72 bytes, porque bcrypt 5 lanza `ValueError` sobre ese límite. |
| 5 | Validar el RUT. | Algoritmo módulo 11 en `ValidadorEntradas.rut`. | Modifico | Se corrigieron las pruebas que usaban `12345678-9` (dígito inválido; el correcto es 5). Para no romper bases antiguas, `listar()` omite la fila inválida y deja el motivo en el log. |
| 6 | Cliente API con timeout y reintentos. | `ClienteSupabase` con `timeout=(3, 10)`, tope de 4 intentos, esperas de 1, 2 y 4 s más azar, y renovación del token ante un 401. | Acepto | Sigue la tabla "Siete fallas, siete respuestas": los 4xx no se reintentan, el 401 renueva una vez, el 429 respeta `Retry-After` y los 5xx, timeouts y fallos de red se reintentan. |
| 7 | ¿Qué hacer si `Retry-After` pide una espera muy larga? | Esperar siempre lo que indique el servidor. | Modifico | Si la espera supera 60 s, falla de forma controlada y pasa al plan B, en vez de dejar la consola bloqueada. |
| 8 | Error de certificado TLS. | `SSLError` hereda de `ConnectionError` y se reintentaría como un fallo de red. | Modifico | Se captura antes y se informa como `ErrorCertificado`, sin reintentar. No se usa `verify=False` en ningún caso. |
| 9 | Deserializar el JSON. | `DeserializadorEmpleados` que usa `.get()`, revisa tipos y largos y delega formato y rangos al dominio. | Acepto | Una fila hostil (RUT inválido, texto en vez de número, nulos, `'; DROP TABLE ...`) se rechaza con su motivo y no detiene las demás. Lo cubre `test_datos_hostiles_se_rechazan_con_su_motivo`. |
| 10 | Persistir lo recibido. | Upsert con `INSERT ... ON CONFLICT DO UPDATE` parametrizado y columna `obtenido_en`. | Acepto | No modifica el departamento ni las jornadas locales, y rechaza una fecha de ingreso posterior a las jornadas ya registradas. |
| 11 | Degradación controlada. | Plan B con los datos locales y su antigüedad; plan C con un aviso si no hay datos. | Acepto | Probado con red real: dominio inexistente, URL `http://` y respuesta 503. Todos terminan con un mensaje claro y sin traceback. |
| 12 | Mostrar los reintentos. | Enviar al log de consola todos los mensajes WARNING y ERROR. | Modifico | La prueba manual mostró el error final duplicado. La consola ahora muestra solo los reintentos; el detalle completo queda en `ecotech.log`, que está fuera de Git. |
| 13 | Auditar las 5 señales. | Revisión manual con `grep`. | Modifico | Se escribió `auditoria.py`, que analiza el AST y consulta PyPI. Se prueba con código vulnerable plantado (`pruebas/test_auditoria.py`), así que la revisión es repetible y no depende de buscar a ojo. |
| 14 | Cumplir la guía de la evaluación formativa, que pide una API de clima o tipo de cambio. | Primero mindicador.cl; como respondió con `SSLError` (el servidor cortó la conexión TLS), propuso Open-Meteo, de clima. | Descarto | El estudiante prefirió el tipo de cambio del dólar. Se probaron otras fuentes y se eligió `open.er-api.com` (ExchangeRate-API): pública, en JSON, con el valor del dólar en pesos. El clima se eliminó del proyecto. Supabase se mantiene para la autenticación Bearer. |
| 15 | Evitar repetir los reintentos en los dos clientes. | Copiar el ciclo de reintentos de `ClienteSupabase` en el nuevo cliente. | Modifico | Se extrajo la clase base `ClienteHTTP` y ambos clientes heredan de ella (herencia y polimorfismo: cada uno redefine `_error_del_cliente`). El 401 de Supabase ahora se renueva con `try/except TokenRechazado`, como en la diapositiva "Cliente API con Bearer y Reintento 401". Las 73 pruebas anteriores pasaron sin cambios. |
| 16 | Tratar las respuestas de la API de tipo de cambio. | Considerar éxito cualquier respuesta 200, como hace `ClienteHTTP`. | Modifico | Al probar la API con un código inexistente respondió **200** con `"result": "error"`. El deserializador revisa `result`, la moneda base, el tipo de cada tasa y la fecha; cualquier dato inválido se trata como `RespuestaInvalida` y activa el plan B. |

## Auditoría de las cinco señales

Se ejecuta con `python auditoria.py` (o `python auditoria.py --sin-red`).

| Señal | Cómo se revisa | Resultado |
| --- | --- | --- |
| SQL dinámico (f-strings, `%`, `.format`) | AST: el texto SQL no debe recibir valores interpolados. | 0 hallazgos. Todas las consultas usan `?`. |
| `verify=False` | AST: argumento `verify=False` o asignación `sesion.verify = False`. | 0 hallazgos. |
| `except Exception: pass` | AST: `except` amplio cuyo cuerpo no hace nada. | 0 hallazgos. |
| Petición sin timeout | AST: llamadas HTTP de `requests` o de una sesión sin `timeout`. | 0 hallazgos. |
| Librerías inexistentes en PyPI | Consulta `https://pypi.org/pypi/<paquete>/<versión>/json`. | Las 10 dependencias de `requirements.txt` existen en la versión fijada. |

Además se revisó el historial con `git log --all -p`: `.env` nunca se subió y
no aparecen claves reales en ningún commit.

También se ejecutó **Bandit**, una de las herramientas que indica la guía
(`bandit -r . -x ./.venv,./pruebas`). Marcó un solo aviso de severidad baja: la constante
`RUTA_TOKEN = "/auth/v1/token"` parecía una contraseña escrita en el código. Era un falso
positivo, porque el valor es una ruta de la API, pero se renombró a `RUTA_AUTENTICACION` para
que el nombre describa lo que guarda. Resultado final: *No issues identified*.

## Pruebas de falla realizadas

- Sin credenciales en `.env`: se informan las variables faltantes y el programa sigue funcionando.
- URL con `http://`: se rechaza antes de enviar credenciales.
- Dominio inexistente (equivale a no tener red): 4 intentos y luego el plan B o C.
- Servidor que responde 503: 4 intentos con espera creciente y luego el plan B o C.
- Token vencido (401), 403, 404, 429 y JSON corrupto: se simulan en `pruebas/test_cliente_supabase.py`.
- Supabase real (18-11-2026): 5 filas, 3 válidas y 2 rechazadas a propósito.
- Tipo de cambio real (19-11-2026): 1 USD = 993,47 CLP guardado; con la API caída, 4 intentos y
  luego el plan B con el último valor, o el plan C si nunca se consultó. El monto
  `100; DROP TABLE tipo_cambio;--` se rechaza antes de llamar a la API.
