# Panel de Mercado en la nube (sin depender del Mac)

GitHub ejecuta el motor cada 15 minutos en horario de mercado (cada 2 horas fuera de él) y publica
el panel en GitHub Pages. Tus datos viajan **cifrados**: la página es pública, pero sin tu contraseña
solo se ve texto ilegible.

| Qué | Dónde queda | ¿Público? |
|---|---|---|
| Código del panel | Repositorio de GitHub | Sí (no contiene datos tuyos) |
| Tus posiciones, carteras comparadas y clasificación | Secreto `PANEL_PRIVATE_JSON` | No: cifrado por GitHub, nadie puede leerlo |
| Contraseña del panel | Secreto `PANEL_PASSWORD` | No |
| Panel publicado | `https://TU-USUARIO.github.io/panel-mercado/` | La página sí; los datos van cifrados (AES-256) |
| Excel, carteras.json, info_posiciones.json | Solo en tu Mac | Nunca se suben (`.gitignore`) |

---

## Puesta en marcha (una sola vez, ~15 minutos)

### 1. Cuenta y repositorio
1. Crea una cuenta en https://github.com/signup (si no tienes).
2. Crea un repositorio nuevo en https://github.com/new
   - Nombre: `panel-mercado`
   - Visibilidad: **Public** (necesario para GitHub Pages gratis y minutos ilimitados)
   - No marques README, .gitignore ni licencia (vacío).

### 2. Subir el código
Claude lo sube desde el Mac. GitHub pide un **token** en vez de contraseña:
1. https://github.com/settings/personal-access-tokens/new (Fine-grained token)
2. Nombre: `panel-mac` · Expiración: 90 días · Repository access: **Only select repositories → panel-mercado**
3. Permissions › Repository permissions: **Contents: Read and write** y **Workflows: Read and write**
4. Generate token y cópialo. Cuando la terminal pida *Password*, pega el token (no se ve al escribir).

### 3. Activar GitHub Pages
Repositorio › **Settings › Pages** › Build and deployment › Source: **GitHub Actions**.

### 4. Guardar los secretos
Repositorio › **Settings › Secrets and variables › Actions › New repository secret**:

| Nombre | Valor |
|---|---|
| `PANEL_PRIVATE_JSON` | Todo el contenido de `secreto_panel.json` (ábrelo con TextEdit, Cmd+A, Cmd+C) |
| `PANEL_PASSWORD` | Una contraseña de **al menos 10 caracteres**, distinta de tus otras cuentas |
| `FRED_API_KEY` | Opcional (hoy FRED funciona sin clave) |

### 5. Primera publicación
Repositorio › **Actions › Actualizar panel › Run workflow**. Tarda 1–2 minutos.
Si termina en verde, el panel queda en `https://TU-USUARIO.github.io/panel-mercado/`.

### 6. En el teléfono
1. Abre la dirección anterior (en Safari si es iPhone).
2. Ingresa tu contraseña y deja marcado *Recordar en este dispositivo*.
3. **iPhone:** Compartir › *Agregar a pantalla de inicio* · **Android:** ⋮ › *Instalar app*.

La versión de computador está en `https://TU-USUARIO.github.io/panel-mercado/panel.html`.

---

## Mantenimiento

- **Cambiaste tu cartera (Excel o carteras.json):** en el Mac ejecuta
  `.venv/bin/python exportar_secreto.py` y pega el nuevo `secreto_panel.json` en el secreto
  `PANEL_PRIVATE_JSON` (Settings › Secrets › Actions › editar). Se aplica en la siguiente actualización.
- **Cambiar la contraseña:** edita el secreto `PANEL_PASSWORD`. El teléfono la pedirá de nuevo.
- **Cerrar sesión en un teléfono:** enlace *Cerrar sesión en este dispositivo* al pie del panel.
- **GitHub pausa las tareas programadas tras 60 días sin cambios en el repositorio.** Te avisará por
  correo; se reactiva con un clic en Actions (o con cualquier cambio en el código).
- **El Mac sigue funcionando igual:** `Iniciar_Panel.command` y `Iniciar_Panel_Movil.command` no cambian.

## Si algo falla
- **El workflow termina en rojo en "Calcular el panel":** el detalle se oculta a propósito (el registro
  de un repositorio público es visible para todos). Lo más común: el secreto `PANEL_PRIVATE_JSON` se
  pegó incompleto, o Yahoo rechazó temporalmente la conexión desde GitHub. Vuelve a ejecutarlo; si
  persiste, prueba en el Mac con `PANEL_PRIVATE_JSON="$(cat secreto_panel.json)" .venv/bin/python panel_mercado.py`.
- **"Contraseña incorrecta" en el teléfono:** revisa mayúsculas; si cambiaste `PANEL_PASSWORD`, usa la nueva.
