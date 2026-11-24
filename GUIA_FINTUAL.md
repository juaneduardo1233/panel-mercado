# Actualización automática desde Fintual

Tocas **"Enviar a mi mail"** en Fintual → un script en tu Gmail lee el certificado → el panel en la nube
toma tus posiciones en su siguiente actualización (15 min en horario de mercado, 2 h fuera de él).

```
Fintual ──correo con PDF──▶ Gmail ──▶ Script de Google (tu cuenta)
                                         │  lee el PDF y guarda: símbolo, empresa, nº de acciones, precio promedio
                                         ▼
           GitHub Actions ──pide con clave secreta──▶ enlace privado del script
```

**Qué sale de tu cuenta de Google:** solo símbolo, nombre de la empresa, número de acciones y precio
promedio. Tu nombre, RUT y el resto del PDF no salen de Google. GitHub no tiene acceso a tu correo.

## Qué certificado pedir en Fintual
Fintual › Perfil › Certificados:

| Certificado | Qué actualiza | Cuándo usarlo |
|---|---|---|
| **Certificado de transacciones y eventos de capital en Acciones** | Cantidades **y precio promedio exacto** (compras, ventas, splits) | Recomendado: la primera vez y después de comprar |
| Certificado de inversión en Acciones (balance) | Solo cantidades y efectivo pendiente | Rápido; si aparece una acción nueva, el script te pedirá el de transacciones |

## Instalación (una vez, ~10 minutos)

1. Abre https://script.google.com con la misma cuenta de Gmail donde te llegan los correos de Fintual
   → **Nuevo proyecto**. Cámbiale el nombre arriba a la izquierda a `Panel Fintual`.
2. Borra todo el contenido de `Código.gs` y pega el contenido de **`gmail_fintual.gs`**. Guarda (Cmd + S).
3. En el menú izquierdo, junto a **Servicios**, toca **+** → elige **Drive API** → **Agregar**.
4. Arriba, en la lista de funciones, elige **`configurar`** y toca **Ejecutar**.
   - Google pedirá permisos: **Revisar permisos** → tu cuenta → **Configuración avanzada** →
     **Ir a Panel Fintual (no seguro)** → **Permitir**. Aparece "no seguro" porque el script es tuyo y
     no pasó por la verificación de Google para apps públicas.
   - En el **Registro de ejecución** (abajo) aparecerá una clave larga: cópiala.
5. En GitHub, crea el secreto **`FINTUAL_KEY`** con esa clave:
   https://github.com/juaneduardo1233/panel-mercado/settings/secrets/actions/new
6. En Apps Script: **Implementar › Nueva implementación** → engranaje → **Aplicación web**
   - Ejecutar como: **Yo** · Quién tiene acceso: **Cualquier usuario** → **Implementar**
   - Copia la **URL de la aplicación web** (termina en `/exec`).
   - "Cualquier usuario" es necesario para que GitHub pueda consultarla; sin la clave secreta no entrega nada.
7. En GitHub, crea el secreto **`FINTUAL_URL`** con esa URL (mismo enlace del paso 5).
8. En Fintual pide el **certificado de transacciones** (Enviar a mi mail). En menos de 10 minutos te llegará
   un correo **"[Panel de Mercado] Panel actualizado desde Fintual"** con el resumen.
9. Para verlo de inmediato: https://github.com/juaneduardo1233/panel-mercado/actions/workflows/panel.yml
   → **Run workflow**. Si no, se aplica solo en la siguiente actualización programada.

## Si algo falla
- **Te llega "No pude leer un certificado de Fintual":** en Apps Script ejecuta `probarUltimoCertificado`
  y revisa el Registro de ejecución (muestra cuántas compras/filas leyó).
- **Ver la clave otra vez:** ejecuta `verClave`.
- **Si GitHub no puede consultar el script,** el panel sigue con las posiciones anteriores (no se rompe).
- **No quieres los correos de aviso:** en el script cambia `AVISAR_POR_CORREO = true` a `false`.

## En el Mac (opcional)
Para que el panel local también use Fintual en vez del Excel, guarda el certificado de transacciones en la
carpeta `certificados/` y ejecuta:
`.venv/bin/python fintual.py certificados/<archivo>.pdf --guardar`
