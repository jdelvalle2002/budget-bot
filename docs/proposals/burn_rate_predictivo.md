# Propuesta Técnica: Sistema de Burn-Rate Predictivo y Control de Ritmo de Gasto (*Pacing Engine*)

Este documento define la arquitectura matemática, algorítmica y de experiencia de usuario para incorporar un motor de análisis predictivo de ritmo de gasto (*Burn-Rate / Pacing*) en el bot de finanzas personales.

---

## 1. Motivación y Diagnóstico

### El Problema de las Alertas Estáticas
Actualmente, los bots y aplicaciones financieras tradicionales utilizan umbrales estáticos basados únicamente en el porcentaje del límite mensual consumido (ej. advertir al alcanzar el 80% o el 100%):
* **Falso Negativo (Ceguera Temprana):** Si un usuario gasta el 70% de su presupuesto de *Salidas* el **día 6 del mes**, el bot no emite ninguna advertencia porque no ha superado el 80%, a pesar de que a ese ritmo financiero el presupuesto se agotará antes de la segunda semana.
* **Falso Positivo (Fatiga de Alertas):** Si el **día 28 del mes** el usuario alcanza el 85% de su presupuesto de *Alimentación*, recibir una alerta alarmista genera ansiedad innecesaria, cuando en realidad su ritmo de gasto ha sido saludable y cerrará el mes dentro de la meta.

### Objetivo
Construir un **Pacing Engine** que evalúe la **velocidad instantánea y proyectada de consumo** en relación con el avance del calendario mensual ($d/D$), transformando las alertas de *reactivas* (cuando el dinero ya se acabó) a *preventivas* (cuando aún hay tiempo de corregir hábitos).

---

## 2. Formulación Matemática del Modelo

### 2.1 Índices y Parámetros Temporales
* $Y \in \mathbb{N}$: Año calendario actual.
* $m \in \{1, \dots, 12\}$: Mes calendario actual.
* $D(Y, m) \in \{28, 29, 30, 31\}$: Días totales del mes en curso.
* $d \in \{1, \dots, D\}$: Día actual de ejecución (`get_local_date().day`).
* $\tau = \frac{d}{D} \in (0, 1]$: Fracción transcurrida del mes (progreso temporal).
* $\tau_{\text{rem}} = 1 - \tau = \frac{D - d}{D}$: Fracción restante del mes.
* $d_{\text{rem}} = D - d$: Días restantes del mes.

### 2.2 Variables Financieras por Categoría
Para cada categoría de gasto $c \in \mathcal{C}_{\text{gasto}}$ que cuente con un presupuesto mensual definido en la pestaña `Config`:
* $L_c \in \mathbb{R}^+$: Límite presupuestario asignado para el mes ($L_c > 0$).
* $G_c^{\text{bruto}}(d) \in \mathbb{R}^+$: Gasto bruto acumulado hasta el día $d$.
* $A_c(d) \in \mathbb{R}^+$: Reembolsos, devoluciones y aportes recibidos en la categoría $c$ hasta el día $d$.
* $G_c(d) = G_c^{\text{bruto}}(d) - A_c(d)$: Gasto neto acumulado a la fecha.
* $\Delta G_c$: Monto de la transacción individual que se acaba de ingresar.

---

### 2.3 Métricas Derivadas del Motor de Pacing

#### 1. Consumo Lineal Esperado ($E_c$)
Representa el monto que teóricamente debería haberse consumido si el gasto se distribuyera de manera uniforme día a día:
$$E_c(d) = L_c \times \tau = L_c \times \left(\frac{d}{D}\right)$$

#### 2. Factor de Ritmo o Burn Ratio ($\beta_c$)
Razón entre el gasto real acumulado y el consumo esperado a la fecha:
$$\beta_c(d) = \frac{G_c(d)}{E_c(d)} = \frac{G_c(d)}{L_c \times \tau} = \frac{\text{Porcentaje de Presupuesto Consumido}}{\text{Porcentaje de Mes Transcurrido}}$$

* **Interpretación de $\beta_c$:**
  * $\beta_c \le 0.85$: **Sub-consumo (Frugal / Seguro).** El ritmo es holgado.
  * $0.85 < \beta_c \le 1.15$: **Ritmo Óptimo (En Meta).** Velocidad alineada con el calendario.
  * $1.15 < \beta_c \le 1.35$: **Atención (Acelerado).** Riesgo moderado de sobregiro.
  * $\beta_c > 1.35$: **Crítico (Sobre-consumo Agudo).** Consumo muy por encima del calendario.

#### 3. Proyección de Gasto a Fin de Mes ($\hat{G}_c$)
Estimación lineal del gasto total con el que cerrará el mes si se mantiene la tasa media observada:
$$\hat{G}_c = \frac{G_c(d)}{d} \times D = \frac{G_c(d)}{\tau}$$
$$\Delta \hat{L}_c = \hat{G}_c - L_c \quad (\text{Desvío proyectado respecto a la meta})$$

#### 4. Día Estimado de Agotamiento ($d_{\text{exhaust}}$)
Día calendario en el que el presupuesto se agotará por completo si se mantiene la velocidad diaria actual $\bar{r}_c = \frac{G_c(d)}{d}$:
$$d_{\text{exhaust}} = \left\lfloor \frac{L_c}{\bar{r}_c} \right\rfloor = \left\lfloor \frac{L_c \times d}{G_c(d)} \right\rfloor = \left\lfloor \frac{d}{\% \text{ consumido}} \right\rfloor$$

#### 5. Presupuesto Diario Seguro Restante ($\bar{s}_c$)
Tasa máxima diaria de gasto admisible desde mañana hasta el final del mes para no violar el límite:
$$\bar{s}_c = \begin{cases} 
\frac{L_c - G_c(d)}{D - d} & \text{si } G_c(d) < L_c \text{ y } d < D \\
0 & \text{si } G_c(d) \ge L_c 
\end{cases}$$

---

## 3. Heurísticas y Tratamiento de Casos Borde

Para evitar alertas engañosas o frustrantes, el modelo incorpora reglas heurísticas indispensables:

### 3.1 Período de Gracia Inicial (Días 1 a 5)
* **Problema:** El día 2 del mes, una compra de supermercado de $60,000 en un presupuesto de $200,000 arroja $\beta = \frac{60000}{200000 \times (2/30)} = 4.5$. Una alerta de "ritmo 350% superior" el día 2 es ruido estadístico debido a la baja cantidad de muestras.
* **Heurística de Silenciamiento:**
  * No disparar alertas automáticas de burn rate durante los primeros $d_{\text{min}} = 5$ días del mes, salvo que el gasto acumulado ya supere el $60\%$ absoluto del presupuesto ($G_c(d) \ge 0.60 \times L_c$).

### 3.2 Gastos Discretos / Agrupados vs. Consumo Continuo
* Categorías como *Cuentas Básicas*, *Hogar* o *Educación* suelen cargarse en 1 o 2 pagos mensuales grandes al inicio del mes.
* **Heurística de Exclusión:**
  * Categorías marcadas como "fijas" o "estructurales" (o donde la frecuencia de transacciones mensuales es típicamente $\le 2$) se excluyen del motor de pacing continuo.
  * El motor de pacing se enfoca en **categorías de gasto variable diario**: *Alimentación*, *Salidas*, *Transporte*, *Café/Snacks*, *Otros Gastos*.

### 3.3 Aportes Superiores al Gasto ($G_c(d) \le 0$)
* Si el saldo neto en una categoría es negativo o cero debido a aportes o reembolsos, $\beta_c \le 0$.
* El motor omite cualquier alerta de sobreconsumo y reporta estado de "Superávit neto".

### 3.4 Histéresis y Prevención de Fatiga de Notificaciones (*Cool-down*)
* **Regla:** El bot no debe alertar sobre la misma categoría en cada transacción subsecuente de $2,000.
* **Mecanismo:**
  * Se almacena en la sesión del usuario o en memoria de caché el último día de alerta emitido por categoría (`last_alert_day[cat]`).
  * Solo se vuelve a emitir alerta si:
    1. Han pasado al menos 3 días desde la última alerta en esa categoría, **O BIEN**
    2. El porcentaje consumido saltó a un nuevo escalón significativo (ej: cruzó el 75%, 90% o 100%).

---

## 4. Diseño de la Experiencia de Usuario (UX)

Se proponen dos canales complementarios para presentar esta inteligencia:

### Canal 1: Micro-Feedback en Confirmación de Gasto (In-Flight)
Cuando el usuario registra un gasto habitual, si la categoría afectada entra en estado de **Atención** o **Crítico** ($\beta_c \ge 1.25$ y $d \ge 5$), se añade un pie sutil a la confirmación habitual:

```text
✅ Gasto Guardado:
- Monto: $18,000
- Concepto: Bar con amigos
- Categoría: Salidas
- Método: Débito

📊 Salidas: $62,000 / $75,000 (83%)
⚠️ Alerta de Ritmo: Al día 14 has consumido el 83% de tu cuota.
A este ritmo agotarás el presupuesto el día 17 (gasto proyectado: $133,000).
💡 Margen seguro restante: $812 / día.
```

### Canal 2: Tablero Diagnóstico On-Demand (`/ritmo` o `/pacing`)
Un comando analítico dedicado que evalúa todo el presupuesto del mes de forma visual y ejecutiva:

```text
⏱️ Diagnóstico de Ritmo de Gasto (Día 14 de 30 • 47% del mes)

🟢 En Meta o Holgados:
• Transporte: $28,000 / $80,000 (35% gastado | Ritmo: 0.7x 🟢)
  └ Proyección: $60,000 | Margen: $3,250/día

🟡 Acelerados:
• Alimentación: $115,000 / $220,000 (52% gastado | Ritmo: 1.1x 🟡)
  └ Proyección: $246,000 | Agotamiento estimado: Día 27

🔴 En Riesgo Crítico:
• Salidas: $62,000 / $75,000 (83% gastado | Ritmo: 1.8x 🔥)
  └ Proyección: $133,000 (Exceso: +$58,000)
  └ Agotamiento estimado: Día 17
  └ Margen seguro restante: $812/día

───────────────────────────────────
💰 Presupuesto Global Variable: $205,000 / $375,000 (55% gastado)
📈 Proyección Cierre Global: $439,000 (Exceso estimado: +$64,000)
```

---

## 5. Arquitectura Técnica de Implementación

### 5.1 Módulo Lógico Propuesto: `src/pacing.py`

```python
"""Motor de cálculo de ritmo de gasto y proyecciones presupuestarias."""
from dataclasses import dataclass
from decimal import Decimal
import calendar
from src.models import get_local_date

@dataclass
class PacingMetric:
    categoria: str
    limite: Decimal
    gasto_neto: Decimal
    porcentaje_gastado: float
    burn_ratio: float
    dia_agotamiento: int | None
    proyeccion_fin_mes: Decimal
    margen_diario_restante: Decimal
    es_alerta: bool
    nivel_severidad: str  # "OK", "WARNING", "CRITICAL"

def compute_category_pacing(categoria: str, limite: Decimal, gasto_neto: Decimal) -> PacingMetric:
    hoy = get_local_date()
    d = hoy.day
    _, D = calendar.monthrange(hoy.year, hoy.month)
    tau = d / D

    if limite <= 0:
        return None

    pct_gastado = float(gasto_neto / limite) * 100.0
    burn_ratio = (pct_gastado / 100.0) / tau if tau > 0 else 1.0

    # Proyecciones
    proyeccion_fin_mes = Decimal(str(round(float(gasto_neto) / tau, 0))) if tau > 0 else gasto_neto
    
    # Día de agotamiento
    if gasto_neto > 0:
        dia_agotamiento = int((float(limite) * d) / float(gasto_neto))
    else:
        dia_agotamiento = None

    dias_restantes = max(1, D - d)
    remanente = max(Decimal(0), limite - gasto_neto)
    margen_diario = remanente / Decimal(dias_restantes)

    # Clasificación de severidad (con filtro de gracia d >= 5)
    es_alerta = False
    severidad = "OK"
    if d >= 5 and gasto_neto > 0:
        if burn_ratio >= 1.35 or (pct_gastado >= 90 and d <= D - 5):
            severidad = "CRITICAL"
            es_alerta = True
        elif burn_ratio >= 1.15:
            severidad = "WARNING"
            es_alerta = True

    return PacingMetric(
        categoria=categoria,
        limite=limite,
        gasto_neto=gasto_neto,
        porcentaje_gastado=pct_gastado,
        burn_ratio=burn_ratio,
        dia_agotamiento=dia_agotamiento,
        proyeccion_fin_mes=proyeccion_fin_mes,
        margen_diario_restante=margen_diario,
        es_alerta=es_alerta,
        nivel_severidad=severidad
    )
```

### 5.2 Dependencias e Impacto
* **Cero impacto en Google Sheets:** Todos los cálculos se realizan en memoria cruzando los datos que ya proveen `load_categories_from_config()` y `get_month_summary()`.
* **Rendimiento:** Complejidad $O(C)$ donde $C$ es el número de categorías (habitualmente $< 20$), ejecutándose en menos de 1 milisegundo.
* **Precisión:** Uso estricto de `decimal.Decimal` para moneda, evitando errores de coma flotante.

---

## 6. Plan de Despliegue por Etapas

| Etapa | Alcance | Entregable |
| :--- | :--- | :--- |
| **Fase 1** | Motor analítico base | Módulo `src/pacing.py` con pruebas unitarias de todos los casos borde (inicios de mes, fin de mes, aportes negativos). |
| **Fase 2** | Comando bajo demanda | Implementación del comando `/ritmo` en Telegram con reporte general visual. |
| **Fase 3** | Alertas en caliente con cooldown | Inyección de micro-alertas en el guardado de gastos con supresión de repetición (3 días). |
