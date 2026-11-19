PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS persona (
    rut     TEXT PRIMARY KEY,
    nombre  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS departamento (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre  TEXT NOT NULL,
    gerente TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS proyecto (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre        TEXT NOT NULL,
    fecha_inicio  DATE NOT NULL,
    descripcion TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS empleado (
    rut              TEXT PRIMARY KEY,
    fecha_ingreso    DATE NOT NULL,
    sueldo_base      REAL NOT NULL CHECK (sueldo_base > 0),
    direccion        TEXT NOT NULL DEFAULT '',
    telefono         TEXT NOT NULL DEFAULT '',
    email            TEXT NOT NULL DEFAULT '',
    tipo_contrato    TEXT NOT NULL DEFAULT 'planta'
                     CHECK (tipo_contrato IN ('planta', 'contratista')),
    bono             REAL NOT NULL DEFAULT 0 CHECK (bono >= 0),
    tarifa           REAL NOT NULL DEFAULT 0 CHECK (tarifa >= 0),
    departamento_id  INTEGER,
    obtenido_en      TEXT,
    FOREIGN KEY (rut) REFERENCES persona(rut) ON DELETE CASCADE,
    FOREIGN KEY (departamento_id) REFERENCES departamento(id)
);

CREATE TABLE IF NOT EXISTS registro_tiempo (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    empleado_rut  TEXT NOT NULL,
    proyecto_id   INTEGER NOT NULL,
    horas         REAL NOT NULL CHECK (horas > 0 AND horas <= 24),
    fecha         DATE NOT NULL,
    descripcion   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (empleado_rut) REFERENCES empleado(rut) ON DELETE CASCADE,
    FOREIGN KEY (proyecto_id) REFERENCES proyecto(id)
);

CREATE TABLE IF NOT EXISTS informe (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo            TEXT NOT NULL,
    fecha_generacion  DATE NOT NULL,
    proyecto_id       INTEGER,
    FOREIGN KEY (proyecto_id) REFERENCES proyecto(id)
);
-- La asignación existe aunque el empleado todavía no haya registrado horas.
CREATE TABLE IF NOT EXISTS empleado_proyecto (
    empleado_rut TEXT NOT NULL,
    proyecto_id INTEGER NOT NULL,
    PRIMARY KEY (empleado_rut, proyecto_id),
    FOREIGN KEY (empleado_rut) REFERENCES empleado(rut) ON DELETE CASCADE,
    FOREIGN KEY (proyecto_id) REFERENCES proyecto(id) ON DELETE CASCADE
);

-- Último clima consultado por ciudad; obtenido_en permite el plan B sin red.
CREATE TABLE IF NOT EXISTS clima (
    consulta      TEXT PRIMARY KEY,
    ciudad        TEXT NOT NULL,
    region        TEXT NOT NULL DEFAULT '',
    latitud       REAL NOT NULL CHECK (latitud BETWEEN -90 AND 90),
    longitud      REAL NOT NULL CHECK (longitud BETWEEN -180 AND 180),
    temperatura   REAL NOT NULL,
    humedad       REAL NOT NULL CHECK (humedad BETWEEN 0 AND 100),
    viento        REAL NOT NULL CHECK (viento >= 0),
    codigo_clima  INTEGER NOT NULL,
    medido_en     TEXT NOT NULL,
    obtenido_en   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usuario (
    nombre TEXT PRIMARY KEY,
    sal TEXT NOT NULL,
    hash_clave TEXT NOT NULL,
    rol TEXT NOT NULL CHECK (rol IN ('admin', 'consulta'))
);
