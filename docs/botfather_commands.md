# Configuración de Comandos en Telegram BotFather

Esta guía detalla cómo configurar la lista oficial de comandos del bot para que los usuarios puedan ver el menú de autocompletado nativo al escribir `/` en Telegram.

---

## 1. Instrucciones de Configuración Paso a Paso

1. Abre tu aplicación de Telegram y busca al bot oficial de Telegram: **[@BotFather](https://t.me/BotFather)**.
2. Inicia la conversación o presiona `/start`.
3. Envía el comando:
   ```text
   /setcommands
   ```
4. BotFather te preguntará a qué bot deseas configurarle los comandos. Selecciona o escribe el `@alias` de tu bot (por ejemplo, `@TuBudgetBot`).
5. Copia y pega en un solo mensaje el bloque de texto exacto que aparece en la sección **2. Lista Oficial de Comandos**.
6. BotFather confirmará con: `Success! Commands list updated.`
7. En tu chat con el bot, los comandos aparecerán automáticamente al pulsar el botón `[/]` junto al campo de escritura.

---

## 2. Lista Oficial de Comandos (Copiar y Pegar)

```text
resumen - Ver resumen mensual y gráfico de gastos
presupuesto - Ver y gestionar presupuestos mensuales
ritmo - Diagnóstico de velocidad de gasto y margen diario
tendencias - Comparar gastos actuales con el mes pasado
ultimas - Ver, editar o borrar últimas transacciones
multi - Registrar múltiples gastos separados por comas
buscar - Buscar transacciones por concepto o detalle
ayuda - Ver instrucciones y ejemplos de uso
cancelar - Cancelar la operación o edición en curso
```

---

## 3. Notas Adicionales
* **Lenguaje Natural:** Recuerda que para el día a día no es necesario usar comandos; puedes escribir libremente frases como *"15k uber"*, *"3500 almuerzo casino pega"* o *"5000 super débito"*.
* **Consultas Inteligentes:** Para preguntas analíticas con IA a tu base de datos, inicia el mensaje con `?` (ej: `? cuánto gasté en salidas este mes`).
