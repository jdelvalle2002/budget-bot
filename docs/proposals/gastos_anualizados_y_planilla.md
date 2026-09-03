# Propuestas de Diseño y Roadmap: Gastos Anualizados y Descuentos por Planilla

Este documento recopila las alternativas arquitectónicas y de modelado financiero para dos casos de uso clave en Budget Bot: la amortización de gastos anualizados y el tratamiento de consumos descontados por planilla laboral.

---

## 1. Gastos Anualizados (Gimnasio, Suscripciones Anuales, Seguros)

### Planteamiento del Problema
Cuando el usuario paga una suscripción anual (por ejemplo, $360.000 de gimnasio o $70.000 de Amazon Prime en un solo pago):
* **Criterio de Flujo de Caja (Caja):** El dinero salió de la cuenta bancaria en una fecha específica.
* **Criterio Devengado (Económico):** El servicio se consume progresivamente durante 12 meses ($30.000 mensuales).

Si se registra como una transacción tradicional única:
1. **Distorsión presupuestaria:** En el mes del pago, la categoría (ej: *Deportes*) dispara alertas críticas de sobregasto (+500% a +1000%).
2. **Meses fantasma:** En los 11 meses restantes la categoría figura en $0, dando una falsa sensación de ahorro o presupuesto disponible.

---

### Alternativas de Implementación

#### Opción A (Recomendada para pago contado): Cuotas Virtuales Mensuales Diferidas
* **Concepto:** Cuando el bot detecta una suscripción o pago anual (ej: *"pagué 360 lucas gimnasio anual"*), distribuye el monto en 12 transacciones mensuales de $30.000 con fecha al 1° de cada mes subsiguiente.
* **Ventajas:**
  - Los reportes mensuales y barras de presupuesto en `/resumen` se mantienen perfectamente comparables y realistas mes a mes.
  - No requiere rediseñar la estructura de Google Sheets ni las consultas analíticas.
* **Detalle técnico:**
  - El parser detecta palabras clave como `"anual"`, `"por el año"`, `"suscripción anual"`.
  - El backend inserta 12 filas en la hoja correspondiente con fechas incrementadas en meses (`fecha.replace(month=...)`).

#### Opción B (Realidad de Tarjeta de Crédito en Cuotas): Registro por Cuota Facturada
* **Concepto:** En Chile, la mayoría de los pagos anuales se realizan en cuotas sin interés mediante tarjeta de crédito (ej: 12 cuotas precio contado).
* **Solución:** No registrar el valor anual en una sola exhibición, sino instruir al bot a registrar la cuota mensual facturada (`$30.000 gimnasio cuota crédito`) mes a mes o proyectar las cuotas comprometidas de la tarjeta.

#### Opción C: Categoría / Tag Amortizable Excluido del Presupuesto Operativo
* **Concepto:** Registrar el monto total en la fecha exacta del pago, pero marcarlo con un flag `es_amortizable = True` o clasificarlo bajo una categoría de nivel superior (ej: *Inversiones / Gastos Anuales*).
* **Comportamiento:**
  - En el balance general histórico se contempla el egreso.
  - En los cálculos de cumplimiento de presupuesto mensual de categorías operativas se excluye para no disparar anomalías ni alertas de quiebra.

---

## 2. Consumos Descontados por Planilla (Casino Laboral) [TODO]

### Planteamiento del Problema
En el trabajo, al consumir en el casino marcando credencial, el cobro no se realiza vía débito ni efectivo en el momento, sino que se descuenta directamente de la liquidación de sueldo a fin de mes.

* Si a fin de mes el usuario registra como ingreso el sueldo **LÍQUIDO** recibido en su cuenta corriente (ej: sueldo base $1.000.000 - $60.000 casino = $940.000 depositados)...
* Y además registró durante el mes 15 almuerzos de $4.000 = $60.000 en Alimentación...
* **Riesgo:** Estaría duplicando la resta del gasto en el balance neto si no se diferencia el método de pago.

---

### Diseño Propuesto

#### 1. Extensión del Modelo de Dominio (`MetodoPago`)
Incorporar el nuevo método al enumerador en `src/models.py`:
```python
class MetodoPago(str, Enum):
    DEBITO = "Débito"
    CREDITO = "Crédito"
    EFECTIVO = "Efectivo"
    TRANSFERENCIA = "Transferencia"
    PLANILLA = "Planilla"  # Descuento por liquidación / planilla
    OTRO = "Otro"
```

#### 2. Detección en Lenguaje Natural
Configurar el prompt de extracción para asociar expresiones como:
* *"marqué credencial en el casino"*, *"almuerzo por planilla"*, *"descuento por planilla"*, *"casino de la pega"*.
* Automáticamente asignar `metodo: "Planilla"`, `categoria: "Alimentación"`.

#### 3. Regla de Balanceo y Conciliación
* **En métricas de consumo y desgloses de categorías (`/resumen`, consultas `?`):**
  - Los gastos con `MetodoPago.PLANILLA` **SÍ se suman** al gasto real de Alimentación, permitiendo saber exactamente cuánto se consumió en almuerzos durante el mes.
* **En el cálculo de Balance Neto (Flujo de Cuenta Bancaria):**
  - Si el usuario ingresa su sueldo neto/líquido, las transacciones con método `Planilla` se marcan como informativas para **no restar nuevamente** del dinero líquido en cuenta corriente.
