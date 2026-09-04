"""Motor de cálculo de ritmo de gasto (Burn-Rate / Pacing) y proyecciones presupuestarias.

Calcula el consumo lineal esperado, el factor de ritmo (burn ratio), el día proyectado de
agotamiento del presupuesto y el margen seguro diario restante para cada categoría presupuestada.
"""

from dataclasses import dataclass
from decimal import Decimal
import calendar
from datetime import date
from typing import Optional

from src.models import format_currency, get_local_date

# Categorías típicamente fijas o estructurales (donde el gasto suele cargarse en un solo pago mensual)
CATEGORIAS_ESTRUCTURALES = {
    "Ahorro", "Inversiones", "Salud", "Cuentas Básicas", "Educación",
    "Remuneraciones", "Otros Ingresos", "Hogar", "Telefonía", "Psicólogo", "Psico","Psicologo"
}

@dataclass
class PacingMetric:
    categoria: str
    limite: Decimal
    gasto_neto: Decimal
    gasto_bruto: Decimal
    aportes: Decimal
    porcentaje_gastado: float
    burn_ratio: float
    dia_agotamiento: Optional[int]
    proyeccion_fin_mes: Decimal
    margen_diario_restante: Decimal
    es_alerta: bool
    nivel_severidad: str  # "OK", "WARNING", "CRITICAL"
    es_estructural: bool


def compute_category_pacing(
    categoria: str,
    limite: Decimal,
    gasto_neto: Decimal,
    gasto_bruto: Optional[Decimal] = None,
    aportes: Optional[Decimal] = None,
    target_date: Optional[date] = None
) -> Optional[PacingMetric]:
    """
    Calcula las métricas de pacing para una categoría con presupuesto asignado.
    Retorna None si el límite es menor o igual a cero.
    """
    if limite <= 0:
        return None

    hoy = target_date or get_local_date()
    d = hoy.day
    _, D = calendar.monthrange(hoy.year, hoy.month)
    tau = d / D

    g_bruto = gasto_bruto if gasto_bruto is not None else (gasto_neto if gasto_neto > 0 else Decimal(0))
    ap = aportes if aportes is not None else Decimal(0)

    # Porcentaje de presupuesto consumido (puede ser negativo si hay aportes a favor)
    pct_gastado = float(gasto_neto / limite) * 100.0

    # Burn ratio (gasto porcentual / tiempo porcentual transcurrido)
    if tau > 0 and gasto_neto > 0:
        burn_ratio = (pct_gastado / 100.0) / tau
    else:
        burn_ratio = 0.0 if gasto_neto <= 0 else 1.0

    # Proyección lineal a fin de mes
    if tau > 0 and gasto_neto > 0:
        proyeccion = Decimal(str(round(float(gasto_neto) / tau, 0)))
    else:
        proyeccion = gasto_neto if gasto_neto > 0 else Decimal(0)

    # Día proyectado de agotamiento del presupuesto
    if gasto_neto > 0:
        dia_agotamiento = int((float(limite) * d) / float(gasto_neto))
    else:
        dia_agotamiento = None

    # Margen seguro diario para los días restantes del mes (d+1 hasta D)
    dias_restantes = max(1, D - d)
    remanente = max(Decimal(0), limite - gasto_neto)
    margen_diario = remanente / Decimal(dias_restantes)

    es_estructural = categoria in CATEGORIAS_ESTRUCTURALES

    # Evaluación de severidad
    severidad = "OK"
    es_alerta = False

    if gasto_neto > 0:
        if pct_gastado > 100:
            severidad = "CRITICAL"
            es_alerta = True
        elif not es_estructural:
            # Período de gracia: primeros 4 días del mes
            if d < 5:
                # En días iniciales solo alerta si el gasto es masivo (>60% del presupuesto mensual)
                if pct_gastado >= 60:
                    severidad = "CRITICAL"
                    es_alerta = True
            else:
                if burn_ratio >= 1.35 or (pct_gastado >= 85 and d <= D - 5):
                    severidad = "CRITICAL"
                    es_alerta = True
                elif burn_ratio >= 1.15:
                    severidad = "WARNING"
                    es_alerta = True

    return PacingMetric(
        categoria=categoria,
        limite=limite,
        gasto_neto=gasto_neto,
        gasto_bruto=g_bruto,
        aportes=ap,
        porcentaje_gastado=pct_gastado,
        burn_ratio=burn_ratio,
        dia_agotamiento=dia_agotamiento,
        proyeccion_fin_mes=proyeccion,
        margen_diario_restante=margen_diario,
        es_alerta=es_alerta,
        nivel_severidad=severidad,
        es_estructural=es_estructural
    )


def format_pacing_report(
    pacing_metrics: list[PacingMetric],
    target_date: Optional[date] = None
) -> str:
    """Genera un reporte analítico visual del ritmo de gasto para Telegram."""
    if not pacing_metrics:
        return "ℹ️ No hay categorías con presupuesto configurado para analizar el ritmo."

    hoy = target_date or get_local_date()
    d = hoy.day
    _, D = calendar.monthrange(hoy.year, hoy.month)
    tau = d / D
    pct_mes = tau * 100.0

    criticos: list[PacingMetric] = []
    advertencias: list[PacingMetric] = []
    en_meta: list[PacingMetric] = []
    estructurales: list[PacingMetric] = []

    total_limite_variable = Decimal(0)
    total_gasto_variable = Decimal(0)
    total_proy_variable = Decimal(0)

    for m in pacing_metrics:
        if m.es_estructural:
            estructurales.append(m)
        else:
            total_limite_variable += m.limite
            total_gasto_variable += m.gasto_neto
            total_proy_variable += m.proyeccion_fin_mes

            if m.nivel_severidad == "CRITICAL":
                criticos.append(m)
            elif m.nivel_severidad == "WARNING":
                advertencias.append(m)
            else:
                en_meta.append(m)

    lineas = [
        f"⏱️ *Diagnóstico de Ritmo de Gasto*\n"
        f"📅 Día {d} de {D} ({hoy.strftime('%m/%Y')}) • *{pct_mes:.0f}% del mes*\n"
    ]

    if criticos:
        lineas.append("🔴 *En Riesgo Crítico:*")
        for m in sorted(criticos, key=lambda x: x.burn_ratio, reverse=True):
            exceso_txt = f" (Excedido por {format_currency(m.gasto_neto - m.limite)})" if m.porcentaje_gastado > 100 else ""
            lineas.append(
                f"• *{m.categoria}:* {format_currency(m.gasto_neto)} / {format_currency(m.limite)} "
                f"(*{m.porcentaje_gastado:.0f}%* | Ritmo: *{m.burn_ratio:.1f}x* 🔥){exceso_txt}"
            )
            if m.dia_agotamiento and m.dia_agotamiento <= D:
                lineas.append(f"  └ ⚠️ Agotamiento estimado: *Día {m.dia_agotamiento}* | Proy: {format_currency(m.proyeccion_fin_mes)}")
            else:
                lineas.append(f"  └ 📈 Proyección fin de mes: *{format_currency(m.proyeccion_fin_mes)}*")
            if m.margen_diario_restante > 0:
                lineas.append(f"  └ 💡 Margen seguro: *{format_currency(m.margen_diario_restante)}/día*")
        lineas.append("")

    if advertencias:
        lineas.append("🟡 *Ritmo Acelerado:*")
        for m in sorted(advertencias, key=lambda x: x.burn_ratio, reverse=True):
            lineas.append(
                f"• *{m.categoria}:* {format_currency(m.gasto_neto)} / {format_currency(m.limite)} "
                f"(*{m.porcentaje_gastado:.0f}%* | Ritmo: *{m.burn_ratio:.1f}x* 🟡)"
            )
            if m.dia_agotamiento and m.dia_agotamiento <= D:
                lineas.append(f"  └ ⏳ Agotamiento: *Día {m.dia_agotamiento}* | Margen: *{format_currency(m.margen_diario_restante)}/día*")
            else:
                lineas.append(f"  └ 📈 Proy: *{format_currency(m.proyeccion_fin_mes)}* | Margen: *{format_currency(m.margen_diario_restante)}/día*")
        lineas.append("")

    if en_meta:
        lineas.append("🟢 *En Meta u Holgados:*")
        for m in sorted(en_meta, key=lambda x: x.porcentaje_gastado, reverse=True):
            if m.gasto_neto <= 0 and m.aportes > 0:
                lineas.append(f"• *{m.categoria}:* {format_currency(m.gasto_neto)} / {format_currency(m.limite)} (🟢 Saldo neto a favor)")
            else:
                lineas.append(
                    f"• *{m.categoria}:* {format_currency(m.gasto_neto)} / {format_currency(m.limite)} "
                    f"({m.porcentaje_gastado:.0f}% | Ritmo: {m.burn_ratio:.1f}x 🟢)"
                )
                lineas.append(f"  └ Margen: *{format_currency(m.margen_diario_restante)}/día* (Proy: {format_currency(m.proyeccion_fin_mes)})")
        lineas.append("")

    if estructurales:
        lineas.append("🏢 *Gastos Fijos / Estructurales:*")
        for m in estructurales:
            lineas.append(f"• *{m.categoria}:* {format_currency(m.gasto_neto)} / {format_currency(m.limite)} ({m.porcentaje_gastado:.0f}%)")
        lineas.append("")

    if total_limite_variable > 0:
        pct_global = float(total_gasto_variable / total_limite_variable) * 100.0
        lineas.append("─────────────────────────")
        lineas.append(f"📊 *Presupuesto Variable Global:*")
        lineas.append(f"• Consumido: *{format_currency(total_gasto_variable)}* / {format_currency(total_limite_variable)} (*{pct_global:.0f}%*)")
        lineas.append(f"• Proyección Cierre: *{format_currency(total_proy_variable)}*")
        if total_proy_variable > total_limite_variable:
            lineas.append(f"• Desvío Proyectado: *+{format_currency(total_proy_variable - total_limite_variable)}* ⚠️")
        else:
            lineas.append(f"• Ahorro Proyectado: *{format_currency(total_limite_variable - total_proy_variable)}* 🟢")

    return "\n".join(lineas)
