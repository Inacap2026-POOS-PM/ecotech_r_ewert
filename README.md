# EcoTech

Proyecto de Programación Orientada a Objeto Seguro TI3021.
Gestiona empleados, departamentos, proyectos y horas trabajadas usando Python y SQLite.

## Preparar y ejecutar en Windows

Desde la carpeta del repositorio:

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python configurar.py
python main.py
```

El archivo `.env` debe tener `DB_MOTOR=sqlite` y `DB_NOMBRE=ecotech.db`.
`configurar.py` crea `.env` y genera una clave de cifrado si falta.
Si ya hay una clave, la conserva. No reemplaces esa clave si ya tienes contactos
cifrados: se necesita para volver a leerlos. `.env` y `.venv` quedan fuera de Git.
El esquema se crea al iniciar. Si la base es la versión anterior del proyecto,
se agregan las columnas nuevas sin borrar sus filas. Los contratos antiguos se
interpretan como empleados de planta con bono cero. Las descripciones antiguas
vacías se muestran como registros anteriores sin descripción.

En VS Code, usa **Python: Select Interpreter** y elige `.venv\Scripts\python.exe`.
Si aparece `No module named dotenv`, estás usando otro intérprete o faltan
las dependencias. Ejecuta `.venv\Scripts\python.exe -m pip install -r requirements.txt`
y usa ese mismo Python para correr el proyecto.

Para ver el ejemplo completo sin modificar la base de trabajo:

```powershell
python main.py --demo
```

## Qué se puede hacer

- Crear, consultar, modificar y eliminar empleados.
- Gestionar departamentos y proyectos con operaciones CRUD.
- Reasignar el departamento de un empleado.
- Asignar un empleado a varios proyectos y varios empleados a un proyecto.
- Registrar horas y consultar los totales y el pago bruto del empleado.
- Sincronizar empleados desde Supabase, con respaldo local si el servicio falla.
- Consultar el tipo de cambio del dólar y convertir un monto a pesos, con respaldo local.

El menú pide un departamento al crear un empleado. El modelo y la base siguen
permitiendo empleados sin departamento para leer los datos de la versión de clase.
Las jornadas se leen junto al empleado para reconstruir sus proyectos y calcular
su pago con las horas guardadas. `ProyectoRepositorio` carga los datos propios del
proyecto; sus colecciones no representan un informe global de todos los empleados.

## Inicio de sesión

La primera ejecución pide crear un administrador con contraseña de al menos
10 caracteres. Después se inicia sesión con esa cuenta. El administrador puede
crear usuarios desde la opción 10. El rol `consulta` solo puede consultar empleados;
el rol `admin` puede gestionar datos y crear cuentas.

Las contraseñas no se guardan como texto ni con cifrado reversible. Se guarda un
hash bcrypt (12 rondas) con una sal aleatoria por usuario; la sal queda incluida en
el hash. Las cuentas creadas con PBKDF2 en la E2 se migran a bcrypt la próxima vez
que inician sesión. bcrypt admite hasta 72 bytes, por lo que se rechazan contraseñas
más largas. Dirección, teléfono y correo se cifran con Fernet antes de
escribirlos en SQLite y se recuperan al reconstruir el objeto. La clave está en
`.env`, separada de las filas de la base. Los datos de contacto de versiones
anteriores se cifran al inicializar el esquema.

Los permisos controlan el uso del menú. El acceso a los archivos locales y a la
clave también depende de los permisos de tu cuenta de Windows. RUT, nombre,
sueldos y horas permanecen sin cifrar; el cifrado implementado cubre los contactos.

## Clases y pagos

`Persona` conserva RUT y nombre, como en el laboratorio. `Empleado` hereda esos
datos y es abstracta. `EmpleadoPlanta` y `EmpleadoContratista` implementan
`calcular_pago()` de distinta forma:

- Planta: sueldo base más bono de sostenibilidad.
- Contratista: tarifa por hora multiplicada por sus horas registradas.

El sueldo base del contratista se conserva como referencia y no se suma al pago
por horas. Los resultados son pagos brutos de las jornadas cargadas; no son una
liquidación mensual ni incluyen impuestos o descuentos legales.

Los datos se validan antes de asignarlos. Los montos deben ser finitos y positivos
(el bono puede ser cero). Las fechas usan `date`. El RUT se conserva como texto,
se acepta con o sin puntos y se comprueba su dígito verificador (módulo 11).
Si una base antigua tiene un RUT inválido, el listado omite esa fila y deja el
motivo en `ecotech.log`.
Correo, dirección y teléfono son opcionales; si se informan, se valida su formato.
Cada jornada admite hasta 24 horas y la suma diaria del empleado también tiene
ese límite, aunque trabaje en proyectos diferentes.

## Servicio externo: Supabase (UA3)

La opción 11 del menú, o `python main.py --sincronizar`, trae los empleados de una
tabla de Supabase, los valida y los guarda en SQLite. Ambas requieren iniciar
sesión como `admin`.

### Configurar Supabase una vez

1. En Supabase, abre **SQL Editor** y ejecuta `db/02_supabase_empleados.sql`.
   Crea la tabla `empleados`, activa RLS con lectura solo para usuarios
   autenticados e inserta datos de ejemplo, entre ellos dos filas inválidas a propósito.
2. En **Authentication > Users**, crea un usuario con correo y contraseña.
3. En **Project Settings > API**, copia la URL y la clave pública (`anon` o
   `publishable`). No uses la clave `service_role`.
4. Completa en `.env` las variables `SUPABASE_URL`, `SUPABASE_KEY`,
   `SUPABASE_EMAIL` y `SUPABASE_PASSWORD`. `.env.example` las lista sin valores reales.

Si falta alguna variable, el programa dice cuáles faltan y el resto del menú sigue
funcionando. La URL debe comenzar con `https://`.

### Cómo se consume

| Módulo | Responsabilidad |
| --- | --- |
| `servicios/cliente_http.py` | Clase base `ClienteHTTP`: timeout, trato por código de estado, reintentos y TLS. |
| `servicios/cliente_supabase.py` | Hereda de `ClienteHTTP`. Inicia sesión, envía `Authorization: Bearer` y renueva el token ante un 401. |
| `servicios/deserializador_empleados.py` | Convierte el JSON en `EmpleadoPlanta` o `EmpleadoContratista` validados. |
| `servicios/sincronizador.py` | Guarda lo recibido o activa el plan B o C. |
| `EmpleadoRepositorio.sincronizar` | Inserta o actualiza con `ON CONFLICT` parametrizado y registra `obtenido_en`. |

Cada petición tiene `timeout=(3, 10)`: 3 s para conectar y 10 s para leer.

| Qué pasa | Respuesta |
| --- | --- |
| Timeout o sin red | Se reintenta. |
| 401 | Se renueva el token una sola vez y se repite la petición. Si vuelve a fallar, se informa. |
| 403, 404 y otros 4xx | No se reintenta; se informa la causa. |
| 429 | Se espera lo que indique `Retry-After`. Si pide más de 60 s, se pasa al plan B. |
| 500 a 503 | Se reintenta con espera creciente. |
| Certificado TLS inválido | Se informa y no se reintenta. Nunca se usa `verify=False`. |

Los reintentos tienen un tope de 4 intentos, con esperas de 1, 2 y 4 s más un
poco de azar. Cada intento se muestra en consola y queda en `ecotech.log`, sin
tokens ni contraseñas.

Cada fila del JSON se trata como un dato hostil. Se revisan tipos, largos, fecha
AAAA-MM-DD no futura, RUT con dígito verificador y formato del correo. Una fila
inválida se rechaza con su motivo y no detiene las demás. La sincronización no
cambia el departamento ni las jornadas registradas localmente.

**Degradación controlada:** si Supabase no responde, se muestran los empleados
guardados en SQLite con la fecha y la antigüedad de su `obtenido_en` (plan B).
Si tampoco hay datos locales, se informa que el servicio no está disponible (plan C).
En ningún caso se muestra una traza de error.

## Servicio externo: tipo de cambio del dólar (UA3)

La guía de la evaluación pide consumir una API de clima o de tipo de cambio. La opción 12
del menú consulta cuántos pesos chilenos vale un dólar en
[ExchangeRate-API](https://www.exchangerate-api.com/docs/free) (`open.er-api.com`), una API
pública que responde JSON y no necesita clave, y convierte el monto que escribe el usuario.
Solo la pueden usar usuarios que iniciaron sesión, con rol `admin` o `consulta`.

1. `ValidadorEntradas.monto` revisa el monto escrito **antes** de cualquier petición: número
   positivo con hasta 2 decimales (punto o coma). Una entrada como
   `100; DROP TABLE tipo_cambio;--` se rechaza y no llega a la API.
2. `ClienteTipoCambio` (hereda de `ClienteHTTP`) pide `GET /v6/latest/USD`. La moneda va en
   la ruta, por eso `ValidadorEntradas.codigo_moneda` exige tres letras y bloquea textos como
   `../admin`. Usa el mismo timeout, reintentos y trato por código que Supabase.
3. `DeserializadorTipoCambio` revisa el JSON con `.get()` y crea un `TipoCambio`, que valida
   que el valor sea un número positivo y finito. La API responde **200** con
   `"result": "error"` cuando algo falla, así que no basta con mirar el código HTTP.
4. `TipoCambioRepositorio` guarda el último valor de cada par de monedas con un upsert
   parametrizado y `obtenido_en`.
5. Si la API no responde o entrega datos inválidos, se convierte con el último valor guardado
   y se indica su antigüedad (plan B); si nunca se consultó, se avisa con claridad (plan C).

### Auditoría de código asistido por IA

```powershell
python auditoria.py
```

Revisa las cinco señales: SQL armado con f-strings, `verify=False`,
`except Exception: pass`, peticiones sin timeout y librerías que no existen en
PyPI. Con `--sin-red` omite la consulta a PyPI. Los resultados y las decisiones
sobre las sugerencias de la IA están en `docs/bitacora_ia.md`.

## Decisiones del laboratorio C08

| Decisión | Elección |
| --- | --- |
| Borrado | Físico con DELETE. El menú pide confirmación. |
| Hijos del empleado | Se borran jornadas y asignaciones con ON DELETE CASCADE. |
| Departamento con empleados | Se impide su eliminación. |
| Proyecto con jornadas o informes | Se impide su eliminación. |
| Identificadores | La base asigna los id numéricos; el RUT identifica al empleado. |

El dominio no ejecuta SQL. Los repositorios usan parámetros y reconstruyen
objetos con funciones de mapeo. La migración usa sentencias completas y fijas,
sin construir SQL con f-strings ni entradas del usuario.
La conexión hace commit o rollback y siempre se cierra. Un RUT duplicado se
informa con `RutDuplicado`, una relación inválida con `RelacionInvalida` y un
problema de acceso a SQLite con `ErrorDeConexion`.

## Pruebas

```powershell
python -m unittest discover -s pruebas -v
```

Las pruebas usan archivos temporales. Comprueban CRUD, contratos, validaciones,
relaciones, inyección SQL, cascada, rollback, actualización de una base antigua,
inicio de sesión, permisos por rol y cifrado de contactos.

Las pruebas de la UA3 no usan la red: simulan las respuestas de Supabase para
cubrir timeout, falta de red, 401 con renovación, 403, 404, 429 con `Retry-After`,
5xx con espera creciente, JSON corrupto, datos hostiles, el upsert con `obtenido_en`
y los planes B y C. También comprueban bcrypt, la migración desde PBKDF2 y que
`auditoria.py` detecte las cinco señales en código vulnerable de ejemplo.
`pruebas/test_tipo_cambio.py` cubre la validación del monto y de la moneda (las entradas
hostiles no llegan a la API), reintentos, 429 y tope del cliente, el error que llega con
código 200, datos hostiles y los planes B y C. En total son 91 pruebas.

Para probar a mano que falla de forma controlada, desconecta la red y usa la opción 12
(dólar) o la 11 (Supabase). También puedes cambiar `SUPABASE_URL` por una dirección
inexistente.

## Diferencias y pendientes del UML de la evaluación 1

Se conservó `Persona` del laboratorio para reutilizar nombre y RUT. Se usó RUT
en lugar del id_empleado numérico del UML. Los nombres `fecha_ingreso` y
`sueldo_base` equivalen a `fecha_inicio` y `salario_base` del informe anterior.
`Informe` es una extensión del material de clase y genera un resumen en memoria.
Las clases de dominio no crean filas: los métodos de creación de datos están en
los repositorios para mantener la separación de responsabilidades.

`GestorAutenticacion` implementa inicio de sesión, revisión de rol y acceso al
cifrado. La autenticación guarda un hash por cuenta en lugar del atributo global
`hash_sistema` del UML. Las propiedades validan cambios, mientras que el inicio
de sesión y los permisos se controlan por separado.
Las bases antiguas conservan sus tipos de columnas y restricciones originales:
la actualización agrega columnas y relaciones, pero no reconstruye las tablas.
Las diferencias con el UML anterior quedan documentadas en el informe ES02;
el equipo debe explicarlas durante la defensa individual.

## Equipo

Sección informada: TI3021. Líder para esta entrega: Rodolfo Ewert.

- Rodolfo Ewert: código base, integración de cambios y coordinación de la entrega.
- Stefan Trostel: revisión general del código junto a Rodolfo y pruebas de funcionamiento.

La entrega grupal usa como base este repositorio. No se asignan a Stefan archivos
específicos que el equipo no haya identificado ni se inventan porcentajes de autoría.
El informe registra el apoyo de IA, los ajustes realizados y las pruebas ejecutadas.
