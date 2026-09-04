# Propuesta Técnica: Mejoras en Control Presupuestario y Experiencia Interactiva (FSM)

Este documento consolida las oportunidades de optimización y evolución arquitectónica identificadas tras la implementación del comando `/presupuesto` y su interfaz interactiva con botones (*Inline FSM*).

---

## 1. Optimizaciones Directas a `/presupuesto`

### 1.1 Edición In-Place de Mensajes (`editMessageText`)
* **Problema:** Enviar mensajes nuevos para cada transición conversacional (reporte ➔ menú de categorías ➔ confirmación) satura el historial del chat.
* **Diseño Propuesto:**
  * Al pulsar `✏️ Modificar Presupuesto`, consumir el endpoint `editMessageText` de la API de Telegram para reemplazar el texto y teclado del mensaje original por la cuadrícula de categorías.
  * Añadir un botón `« Volver al Reporte` que reescriba el mensaje con el resumen consolidado.
* **Ventajas:** Experiencia fluida similar a una mini-aplicación móvil dentro de un único globo de mensaje.

### 1.2 Badges Visuales de Estado en Botones
* **Problema:** El usuario no sabe qué límite tiene asignado una categoría hasta que pulsa sobre ella.
* **Diseño Propuesto:**
  * Construir dinámicamente las etiquetas de los botones con el estado actual:
    ```text
    [ 🟢 Alimentación ($230k) ] [ ⚪ Deportes (Sin límite) ]
    [ 🟢 Hogar ($80k) ]         [ 🟢 Transporte ($80k) ]
    [ 🟢 Salidas ($75k) ]       [ ⚪ Salud (Sin límite) ]
    ```
* **Ventajas:** Información visual inmediata que agiliza la toma de decisiones al editar.

### 1.3 Atajo Directo / One-Shot (Enfoque Híbrido Completo)
* **Problema:** La navegación por botones toma varios toques cuando el usuario ya sabe exactamente qué categoría y monto desea modificar.
* **Diseño Propuesto:**
  * Soportar parámetros opcionales en el comando:
    * `/presupuesto <categoria> <monto>` (ej: `/presupuesto salidas 90k` o `/presupuesto hogar 100000`).
    * `/presupuesto <categoria> 0` (o `borrar`) para eliminar el límite.
  * Si se invoca sin argumentos (`/presupuesto`), mantiene el comportamiento actual (reporte + menú interactivo).
* **Ventajas:** Máxima eficiencia para usuarios frecuentes (cero fricción).

---

## 2. Inteligencia Financiera y Monitoreo de Presupuesto

### 2.1 Vista de Presupuesto Restante / Disponible en Tiempo Real
* **Concepto:** Complementar el reporte estático de límites cruzándolo con el gasto acumulado del mes en curso.
* **Estructura del Mensaje:**
  ```text
  🎯 Estado de Presupuestos (Mes Actual)

  • Alimentación: $140,000 / $230,000 (🟢 Quedan $90,000 | 61%)
  • Salidas: $78,000 / $75,000 (🔴 Excedido por $3,000 | 104%)
  • Transporte: $35,000 / $80,000 (🟢 Quedan $45,000 | 44%)

  ─────────────────────────
  💰 Total: $253,000 / $385,000 gastado (🟢 Disponible neto: $132,000)
  ```

### 2.2 Alerta Predictiva de Ritmo de Gasto (*Burn-Rate / Pacing*)
* Para la formulación matemática formal, heurísticas anti-fatiga, modelos de estacionalidad y arquitectura detallada, consultar la propuesta dedicada: [docs/proposals/burn_rate_predictivo.md](burn_rate_predictivo.md).

---

## 3. Extensión del Patrón Inline FSM a Otras Funcionalidades

### 3.1 Edición Granular en `/ultimas`
* **Situación actual:** La edición solicita reescribir la transacción completa en lenguaje natural, reprocesándola con el LLM.
* **Diseño Propuesto:** Al pulsar `✏️ Editar`, desplegar botones de acción específica:
  ```text
  [ 📁 Cambiar Categoría ] [ 💳 Cambiar Método ] [ 💵 Cambiar Monto ]
  ```
  * Permite cambiar de *Débito* a *Planilla* en 1 clic.
  * Permite reasignar categoría mediante la misma botonera de categorías.

### 3.2 Gestión Dinámica de Categorías desde Telegram
* **Caso de uso:** Añadir o desactivar categorías sin abrir Google Sheets en el navegador.
* **Flujo:** Comando `/categoria nueva <Nombre> <ColorHex>` o asistente interactivo que actualiza `Config!A:B` y refresca las opciones disponibles para el parser.

### 3.3 Invalidación de Caché con TTL (Time-To-Live)
* **Situación actual:** `_cached_categories` se almacena indefinidamente hasta que ocurre un reinicio del proceso o un guardado local con invalidación forzada.
* **Diseño Propuesto:**
  * Incorporar un timestamp de expiración (`TTL = 300` segundos).
  * Si el usuario realiza cambios manuales directamente en Google Sheets desde su navegador, el bot detectará automáticamente los nuevos valores al expirar el TTL sin requerir reinicios.

---

## 4. Matriz de Priorización

| Fase | Funcionalidad | Complejidad | Impacto UX |
| :--- | :--- | :--- | :--- |
| **Fase 1 (Inmediata)** | Actualización *In-Place* (`editMessageText`) y Atajo Directo (`/presupuesto cat monto`) | Baja | Alto |
| **Fase 2 (Corto Plazo)** | Badges con montos en botones y visualización de Disponible Restante | Media | Alto |
| **Fase 3 (Medio Plazo)** | Edición granular en `/ultimas` y Caché con TTL | Media | Muy Alto |
| **Fase 4 (Avanzada)** | Alertas de *Burn-Rate / Pacing* predictivo (ver [burn_rate_predictivo.md](burn_rate_predictivo.md)) | Media-Alta | Alto |
