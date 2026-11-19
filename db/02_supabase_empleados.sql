-- Ejecutar una vez en Supabase > SQL Editor. Crea la tabla que consume EcoTech.
create table if not exists public.empleados (
    rut            text primary key,
    nombre         text not null,
    fecha_ingreso  date not null,
    sueldo_base    integer not null,
    tipo_contrato  text not null,
    bono           integer default 0,
    tarifa_hora    integer,
    email          text,
    telefono       text,
    direccion      text
);

-- Sin una política, la tabla no se puede leer desde la API.
alter table public.empleados enable row level security;

drop policy if exists "Lectura para usuarios autenticados" on public.empleados;
create policy "Lectura para usuarios autenticados" on public.empleados
    for select to authenticated using (true);

insert into public.empleados
    (rut, nombre, fecha_ingreso, sueldo_base, tipo_contrato, bono, tarifa_hora,
     email, telefono, direccion)
values
    ('15234567-4', 'Camila Soto', '2023-03-01', 1100000, 'planta', 80000, null,
     'camila.soto@ecotech.cl', '+56911112222', 'Av. Sol 10, Santiago'),
    ('18765432-7', 'Diego Pérez', '2024-07-15', 900000, 'contratista', 0, 12000,
     'diego.perez@ecotech.cl', null, null),
    ('9876543-3', 'Marta Lillo', '2021-01-04', 1350000, 'planta', 100000, null,
     null, '+56922223333', 'Los Robles 45, Temuco'),
    -- Filas inválidas a propósito: EcoTech debe rechazarlas sin detenerse.
    ('20111222-9', 'RUT con dígito incorrecto', '2024-01-01', 900000, 'planta', 0, null,
     null, null, null),
    ('16543210-K', 'Correo inválido', '2024-01-01', 900000, 'planta', 0, null,
     'no-es-un-correo', null, null)
on conflict (rut) do nothing;
