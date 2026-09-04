"""Pruebas unitarias y de integración para el motor de Burn-Rate y Pacing."""

import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from src.models import format_currency
from src.pacing import (
    PacingMetric,
    compute_category_pacing,
    format_pacing_report,
    CATEGORIAS_ESTRUCTURALES,
)
from src import main


def test_compute_category_pacing_normal():
    print("\n--- TEST: Pacing Normal en Mitad de Mes ---")
    # Día 15 de un mes de 30 días, límite 100k, gastado 50k
    target = date(2026, 9, 15)
    metric = compute_category_pacing(
        categoria="Alimentación",
        limite=Decimal("100000"),
        gasto_neto=Decimal("50000"),
        target_date=target,
    )
    assert metric is not None
    assert metric.porcentaje_gastado == 50.0
    assert round(metric.burn_ratio, 2) == 1.0
    assert metric.dia_agotamiento == 30
    assert metric.proyeccion_fin_mes == Decimal("100000")
    assert metric.margen_diario_restante == Decimal("50000") / Decimal("15")
    assert metric.nivel_severidad == "OK"
    assert metric.es_alerta is False
    print("  ✅ Pacing 1.0x (en meta) computado correctamente.")


def test_compute_category_pacing_warning_and_critical():
    print("\n--- TEST: Pacing Acelerado (Warning) y Crítico ---")
    target = date(2026, 9, 15)  # 50% del mes
    # Warning: 60% gastado al día 15 -> burn_ratio = 60 / 50 = 1.2x
    metric_w = compute_category_pacing(
        categoria="Salidas",
        limite=Decimal("100000"),
        gasto_neto=Decimal("60000"),
        target_date=target,
    )
    assert metric_w.nivel_severidad == "WARNING"
    assert metric_w.es_alerta is True
    assert metric_w.dia_agotamiento == 25  # 100000 * 15 / 60000 = 25
    print(f"  ✅ Warning OK: Ritmo {metric_w.burn_ratio:.1f}x, agota día {metric_w.dia_agotamiento}.")

    # Critical: 80% gastado al día 15 -> burn_ratio = 80 / 50 = 1.6x
    metric_c = compute_category_pacing(
        categoria="Salidas",
        limite=Decimal("100000"),
        gasto_neto=Decimal("80000"),
        target_date=target,
    )
    assert metric_c.nivel_severidad == "CRITICAL"
    assert metric_c.es_alerta is True
    assert metric_c.dia_agotamiento == 18  # 100000 * 15 / 80000 = 18.75 -> 18
    print(f"  ✅ Critical OK: Ritmo {metric_c.burn_ratio:.1f}x, agota día {metric_c.dia_agotamiento}.")


def test_grace_period():
    print("\n--- TEST: Período de Gracia (Días 1 a 4) ---")
    # Día 2 de 30 días, compra de 30k en 100k -> 30% gastado
    # Sin gracia sería 30 / (2/30) = 4.5x, pero con gracia no debe alertar
    target_early = date(2026, 9, 2)
    metric_grace = compute_category_pacing(
        categoria="Alimentación",
        limite=Decimal("100000"),
        gasto_neto=Decimal("30000"),
        target_date=target_early,
    )
    assert metric_grace.es_alerta is False
    assert metric_grace.nivel_severidad == "OK"
    print("  ✅ Período de gracia silenció alerta prematura el día 2.")

    # Día 2 con gasto masivo (>60%): debe alertar a pesar de ser día 2
    metric_massive = compute_category_pacing(
        categoria="Alimentación",
        limite=Decimal("100000"),
        gasto_neto=Decimal("65000"),
        target_date=target_early,
    )
    assert metric_massive.es_alerta is True
    assert metric_massive.nivel_severidad == "CRITICAL"
    print("  ✅ Gasto masivo (>60%) en día 2 sí gatilló alerta crítica.")


def test_structural_categories_and_reimbursements():
    print("\n--- TEST: Categorías Estructurales y Saldos a Favor ---")
    target = date(2026, 9, 10)
    # Cuentas Básicas cargada el día 10 al 90% (gasto estructural normal)
    metric_struct = compute_category_pacing(
        categoria="Cuentas Básicas",
        limite=Decimal("100000"),
        gasto_neto=Decimal("90000"),
        target_date=target,
    )
    assert metric_struct.es_estructural is True
    assert metric_struct.es_alerta is False  # No debe alertar como sobreconsumo diario
    print("  ✅ Categoría estructural identificada y no genera alerta falsa.")

    # Reembolso mayor al gasto (neto <= 0)
    metric_reimb = compute_category_pacing(
        categoria="Alimentación",
        limite=Decimal("100000"),
        gasto_neto=Decimal("-20000"),
        target_date=target,
    )
    assert metric_reimb.es_alerta is False
    assert metric_reimb.burn_ratio == 0.0
    print("  ✅ Saldo neto a favor manejado limpiamente sin alertas.")


def test_format_pacing_report():
    print("\n--- TEST: Formato de Reporte Markdown (/ritmo) ---")
    target = date(2026, 9, 15)
    metrics = [
        compute_category_pacing("Salidas", Decimal("100000"), Decimal("80000"), target_date=target),
        compute_category_pacing("Transporte", Decimal("80000"), Decimal("30000"), target_date=target),
        compute_category_pacing("Alimentación", Decimal("200000"), Decimal("120000"), target_date=target),
        compute_category_pacing("Cuentas Básicas", Decimal("90000"), Decimal("85000"), target_date=target),
    ]
    reporte = format_pacing_report(metrics, target_date=target)
    assert "Diagnóstico de Ritmo de Gasto" in reporte
    assert "En Riesgo Crítico" in reporte
    assert "Salidas" in reporte
    assert "En Meta u Holgados" in reporte
    assert "Gastos Fijos / Estructurales" in reporte
    assert "Presupuesto Variable Global" in reporte
    print("  ✅ Reporte analítico formateado con todas sus secciones.")


async def test_bot_ritmo_command_and_callbacks():
    print("\n--- TEST: Comando /ritmo, Botón en /presupuesto y Callback ---")
    mock_config = {
        "Alimentación": {"color": "#E74C3C", "presupuesto": 200000.0},
        "Salidas": {"color": "#E67E22", "presupuesto": 80000.0},
    }
    mock_resumen = {
        "Alimentación": {"total": 90000.0, "gasto_bruto": 90000.0, "aportes": 0.0, "count": 5},
        "Salidas": {"total": 70000.0, "gasto_bruto": 70000.0, "aportes": 0.0, "count": 4},
    }

    captured_messages = []

    async def mock_enviar_mensaje(chat_id, text, parse_mode="Markdown", reply_markup=None):
        captured_messages.append({"text": text, "reply_markup": reply_markup})

    with patch.object(main.sheets_client, "load_categories_from_config", return_value=mock_config), \
         patch.object(main.sheets_client, "get_month_summary", return_value=(mock_resumen, 9, 2026)), \
         patch.object(main, "enviar_mensaje_telegram", side_effect=mock_enviar_mensaje):

        # 1. Probar comando /ritmo
        await main.process_telegram_update(chat_id="12345", text="/ritmo", message_id="1")
        assert len(captured_messages) >= 2
        last_msg = captured_messages[-1]["text"]
        assert "Diagnóstico de Ritmo de Gasto" in last_msg
        assert "Salidas" in last_msg
        print("  ✅ Comando /ritmo responde con el reporte de pacing.")

        # 2. Probar comando /presupuesto con botón hacia /ritmo
        captured_messages.clear()
        await main.process_telegram_update(chat_id="12345", text="/presupuesto", message_id="2")
        presup_msg = captured_messages[-1]
        assert "Presupuesto Mensual Definido" in presup_msg["text"]
        assert "/ritmo" in presup_msg["text"]
        markup = presup_msg["reply_markup"]
        btn_callbacks = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
        assert "pacing_view" in btn_callbacks
        print("  ✅ Comando /presupuesto incluye acceso directo y botón [⏱️ Ver Ritmo].")

        # 3. Probar callback query pacing_view
        captured_messages.clear()
        await main.process_telegram_callback(chat_id="12345", callback_data="pacing_view")
        callback_msg = captured_messages[-1]["text"]
        assert "Diagnóstico de Ritmo de Gasto" in callback_msg
        print("  ✅ Callback pacing_view genera el diagnóstico interactivo correctamente.")


async def run_all():
    test_compute_category_pacing_normal()
    test_compute_category_pacing_warning_and_critical()
    test_grace_period()
    test_structural_categories_and_reimbursements()
    test_format_pacing_report()
    await test_bot_ritmo_command_and_callbacks()
    print("\n🎉 TODOS LOS TESTS DE BURN RATE Y PACING PASARON EXITOSAMENTE.")


if __name__ == "__main__":
    asyncio.run(run_all())
