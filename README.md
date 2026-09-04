# Budget Bot

Este es un proyecto personal que armé para gestionar mis finanzas y llevar un registro de mis gastos directamente en Google Sheets, pero usando un bot de Telegram impulsado por la IA de Google (Gemini) para no tener que llenar formularios a mano. Lo comparto por si a alguien más le sirve la idea o el código.

La gracia principal es que le puedes hablar en lenguaje natural (por ejemplo: *"gasté 15 lucas en uber ayer"*) y la IA se encarga de extraer el monto, la categoría y la fecha para insertarlo estructurado en la planilla.

---

## Funcionalidades

*   **Ingreso por lenguaje natural:** Escribes tu gasto de forma casual y el bot extrae los datos usando Gemini (ideal para el día a día).
*   **Ingreso múltiple (`/multi`):** Permite ingresar varios gastos de una sola vez separados por coma.
*   **Resúmenes visuales (`/resumen`):** El bot genera un gráfico circular con lo gastado en el mes (o el mes anterior) directamente en el chat.
*   **Consulta de Presupuesto (`/presupuesto`):** Consulta en tiempo real tus límites mensuales definidos en la pestaña `Config` y el total presupuestado.
*   **Consultas (`?`):** Le puedes hacer preguntas financieras a la IA sobre tu propia base de datos (ej. *"¿cuánto gasté en comida la semana pasada?"*).
*   **Edición desde Telegram:** Permite revisar y borrar/editar los últimos registros usando botones integrados en el chat.
*   **Neteo Automático de Gastos:** Si registras un "Ingreso" en una categoría de gasto tradicional (por ejemplo, cuando un amigo te transfiere su parte del supermercado a "Alimentación"), el bot automáticamente *restará* ese ingreso del total de gastos en los resúmenes y consultas, manteniendo tus presupuestos limpios y reales.
*   **Sin estado (Stateless):** El bot no guarda estado persistente local (usa una máquina de estados efímera) para poder desplegarlo en la nube sin problemas.

---

## Arquitectura y Stack

*   **Lenguaje:** Python 3.12+
*   **Framework:** FastAPI (recibe los webhooks de Telegram).
*   **IA:** Google GenAI SDK (`gemini-flash-lite-latest`) con *Structured Outputs* (JSON).
*   **Base de Datos:** Google Sheets (API V4) usando una Service Account.
*   **Despliegue:** Yo actualmente lo tengo corriendo en [Render](https://render.com) en su capa gratuita (Web Service). Al usar FastApi y Gunicorn, es muy fácil de desplegar.

---

## Cómo usarlo o probarlo

Si quieres clonar el proyecto y usarlo para ti, necesitas:
1. Un Token de Telegram (se crea hablando con BotFather).
2. Una API Key de Gemini.
3. Una Service Account de Google Cloud con permisos para usar la API de Google Sheets.

### 1. Entorno local

```bash
git clone https://github.com/tu-usuario/budget-bot.git
cd budget-bot
python -m venv venv
# En Windows: .\venv\Scripts\activate
# En Mac/Linux: source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```
Completa el archivo `.env` con tus credenciales. Asegúrate de poner tu ID de Telegram en `USER_ID` para que el bot ignore a otras personas.

### 2. Configurar la planilla (Google Sheets)

Para no armar las columnas a mano, hice un script que te configura una hoja en blanco automáticamente:
1. Crea una Google Sheet vacía y compártela (permisos de Editor) con el mail de tu Service Account de Google.
2. Copia el ID de la planilla (está en la URL).
3. Ejecuta:
```bash
python scripts/setup_sheet.py TU_SHEET_ID_AQUI
```
Esto creará la hoja del año actual, una pestaña `Config` y agregará validaciones de celdas.

**💡 Tip - Presupuestos Mensuales:** 
En la pestaña `Config`, la columna C se llama "Presupuesto". Si quieres que el bot controle tus gastos, ingresa un límite numérico ahí (ej: `200000`).
- En el comando `/resumen`, las categorías con presupuesto mostrarán una barra de progreso.
- Al ingresar gastos en categorías estrictas (ej. Salidas, Telefonía, etc), la IA te avisará o se pondrá pesado si te pasas de tu presupuesto.

### 3. Ejecutar y Desplegar
Localmente puedes probarlo con:
```bash
uvicorn src.main:app --reload
```
*(Para que Telegram se comunique con tu entorno local necesitarás usar ngrok o similar).*

Para **producción**, yo conecté el repositorio a un *Web Service* en **Render**, apuntando el comando de inicio a Uvicorn/Gunicorn. Al estar construido con FastAPI, funciona perfectamente recibiendo el tráfico como Webhook de Telegram.

---

## Próximas Mejoras (Roadmap / TODO)

* [ ] **Gastos Anualizados:** Implementar distribución diferida en cuotas virtuales para compras anuales (gimnasio, seguros, suscripciones anuales) para evitar distorsiones en los presupuestos mensuales de caja.
* [ ] **Método de Pago `Planilla`:** Soporte para consumos descontados por planilla laboral (ej. casino de la empresa con credencial), contabilizándolos en el gasto real de alimentación pero excluyéndolos de la resta del balance bancario neto.

Para más detalles de arquitectura y diseño contable, consultar [docs/proposals/gastos_anualizados_y_planilla.md](docs/proposals/gastos_anualizados_y_planilla.md).
