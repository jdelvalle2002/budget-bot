# 🤖 Budget Bot: Asistente Financiero con Inteligencia Artificial

Un bot personal para gestionar finanzas y gastos, integrando **Telegram** con **Google Sheets** utilizando el poder de **Google Gemini AI**.
Olvídate de ingresar datos llenando formularios pesados; simplemente chatea con el bot usando lenguaje natural y él se encargará de extraer montos, categorías y fechas para mantener tu libro contable (ledger) impecable.

---

## ✨ Funcionalidades Principales

*   🧠 **Extracción por Lenguaje Natural:** Escribe *"gasté 15 lucas en uber ayer"* y el bot lo convertirá mágicamente en un registro estructurado (`Monto: 15000, Categoría: Transporte, Método: Débito`).
*   📦 **Registro Múltiple (`/multi`):** Agrega varios gastos de golpe en un solo mensaje separados por comas (ej. `/multi 15k uber, 30k super, 5k helado`).
*   📊 **Dashboard y Gráficos (`/resumen`):** Pide un resumen del mes actual o `/resumen anterior` para generar en tiempo real un gráfico circular (*Pie Chart*) con tu distribución de gastos.
*   💡 **Consultas Analíticas (`?`):** Pregúntale a la IA sobre tus finanzas. Ej: `? cuánto he gastado en comida este mes comparado con el anterior`.
*   ✏️ **Edición Inline:** Revisa tus últimos registros con `/ultimas` y usa botones interactivos de Telegram para Editar o Borrar un registro sin tocar la base de datos.
*   🛡️ **Idempotencia y Estado Aislado:** Arquitectura *stateless* tolerante a reintentos de red, con una FSM (Máquina de Estados) por usuario.

---

## 🏗️ Arquitectura Técnica

*   **Backend:** Python 3.12+, FastAPI, Pydantic.
*   **Inteligencia Artificial:** Google GenAI SDK (`gemini-flash-lite-latest`) con *Structured Outputs* estrictos (JSON Schema).
*   **Base de Datos:** Google Sheets (API V4) a través de una Service Account. Funciona como un Data Lake plano (una hoja de registro por año).
*   **Despliegue:** Preparado para despliegue serverless o en contenedores (ej. Render) mediante un Webhook asíncrono para Telegram.

---

## 🚀 Guía de Instalación y Setup

### 1. Requisitos Previos
*   Un Token de **Telegram Bot** (creado vía BotFather).
*   Una **API Key de Gemini** de Google AI Studio.
*   Una **Service Account de Google Cloud** con permisos habilitados para Google Sheets API.

### 2. Clonar y Configurar Entorno
```bash
git clone https://github.com/tu-usuario/budget-bot.git
cd budget-bot
python -m venv venv
# Activar entorno (Windows)
.\venv\Scripts\activate
# (Mac/Linux: source venv/bin/activate)

pip install -r requirements.txt
cp .env.example .env
```

Rellena las variables de tu archivo `.env` con tus tokens y secretos. Asegúrate de incluir la ID de usuario (`USER_ID`) permitida para evitar que extraños usen tu bot.

### 3. Setup Automatizado de Base de Datos
El proyecto incluye un script mágico para convertir cualquier Google Sheet vacía en una base de datos restrictiva.
1. Crea una Google Sheet y compártela con el email de tu *Service Account*.
2. Copia el **ID de la Sheet** (está en la URL).
3. Corre el script de setup:
```bash
python scripts/setup_sheet.py TU_SHEET_ID_AQUI
```
Este script creará la pestaña del año actual, la pestaña dinámica de `Config` (con colores de categorías) y aplicará validaciones de datos en las columnas. Sigue las instrucciones impresas en consola para agregar la macro auto-ID.

### 4. Ejecución Local (Pruebas)
Para probar el servidor de webhooks localmente (necesitarás ngrok o similar para recibir los pings de Telegram):
```bash
uvicorn src.main:app --reload
```

---

*Desarrollado para la gestión de finanzas personales automatizadas y sin fricción.*
