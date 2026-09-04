"""Pruebas unitarias para el parsing flexible de fechas y el comando /tendencias."""

import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock

from src.models import parse_flexible_date, format_currency
from src.sheets_client import GoogleSheetsClient
from src import main


def test_parse_flexible_date():
    print("\n--- TEST: parse_flexible_date ---")
    # ISO formats
    assert parse_flexible_date("2026-09-04") == date(2026, 9, 4)
    assert parse_flexible_date("2026-09-04T15:30:00") == date(2026, 9, 4)
    assert parse_flexible_date("2026/09/04") == date(2026, 9, 4)

    # Latin formats (DD-MM-YYYY / DD/MM/YYYY)
    assert parse_flexible_date("13-8-2026") == date(2026, 8, 13)
    assert parse_flexible_date("13/08/2026") == date(2026, 8, 13)
    assert parse_flexible_date("01-09-2026") == date(2026, 9, 1)

    # 2-digit year (DD-MM-YY)
    assert parse_flexible_date("13-08-26") == date(2026, 8, 13)

    # Direct date/datetime instances
    d = date(2026, 8, 20)
    assert parse_flexible_date(d) == d

    # Invalid / None
    assert parse_flexible_date(None) is None
    assert parse_flexible_date("") is None
    assert parse_flexible_date("no-es-fecha") is None
    print("  ✅ parse_flexible_date resolvió correctamente todos los formatos.")


def test_get_month_summary_includes_latin_dates():
    print("\n--- TEST: get_month_summary con formatos latinos (13-8-2026) ---")
    client = GoogleSheetsClient.__new__(GoogleSheetsClient)
    client.spreadsheet_id = "mock_sheet"

    mock_values = [
        ["ID", "Fecha", "Tipo", "Monto", "Concepto", "Categoría", "Método", "Comentarios"],
        # Formato ISO estándar
        ["1", "2026-08-11", "Gasto", "5000", "Almuerzo", "Alimentación", "Débito", ""],
        # Formato latino DD-MM-YYYY (el caso real encontrado en sheets)
        ["2", "13-8-2026", "Gasto", "7500", "Reembolso almuerzo", "Alimentación", "Débito", ""],
        # Formato latino con barras
        ["3", "20/08/2026", "Gasto", "10000", "Super", "Alimentación", "Débito", ""],
    ]

    mock_get = MagicMock()
    mock_get.execute.return_value = {"values": mock_values}
    mock_resource = MagicMock()
    mock_resource.get.return_value = mock_get
    client.sheet = MagicMock()
    client.sheet.values.return_value = mock_resource

    # Target: Agosto 2026 (mes offset de -1 si estamos en Septiembre 2026)
    with patch("src.models.get_local_date", return_value=date(2026, 9, 4)):
        res, m, y = client.get_month_summary(month_offset=-1)

    assert m == 8
    assert y == 2026
    # Debe haber sumado las 3 transacciones: 5000 + 7500 + 10000 = 22500
    assert res["Alimentación"]["gasto_bruto"] == 22500.0
    assert res["Alimentación"]["count"] == 3
    print("  ✅ get_month_summary sumó transacciones con formatos mixtos exitosamente.")


async def test_tendencias_fallback_when_past_mtd_is_zero():
    """Prueba el escenario exacto del usuario: 0 gastos en días 1-4 del mes pasado, pero gastos totales > 0."""
    print("\n--- TEST: /tendencias con fallback inteligente (días 1-4 en $0) ---")
    
    # Mock data
    mock_act_mtd = {
        "Alimentación": {"total": 21456.0, "gasto_bruto": 21456.0, "aportes": 0.0},
        "Hogar": {"total": 14071.0, "gasto_bruto": 14071.0, "aportes": 0.0},
        "Salud": {"total": -180000.0, "gasto_bruto": 0.0, "aportes": 180000.0},
    }
    # En días 1 a 4 del mes pasado: vacío (gasto = 0)
    mock_pas_mtd = {}
    # En el mes pasado completo: $281,929
    mock_pas_full = {
        "Alimentación": {"total": 160136.0, "gasto_bruto": 160136.0, "aportes": 0.0},
        "Salidas": {"total": 85650.0, "gasto_bruto": 85650.0, "aportes": 0.0},
        "Transporte": {"total": 36143.0, "gasto_bruto": 36143.0, "aportes": 0.0},
    }

    captured_messages = []

    async def mock_enviar(chat_id, text, **kwargs):
        captured_messages.append(text)

    def mock_summary(offset, max_day=None):
        if offset == 0:
            return mock_act_mtd, 9, 2026
        elif offset == -1:
            if max_day == 4:
                return mock_pas_mtd, 8, 2026
            return mock_pas_full, 8, 2026
        return {}, 0, 0

    with patch.object(main.sheets_client, "get_month_summary", side_effect=mock_summary), \
         patch("src.models.get_local_date", return_value=date(2026, 9, 4)), \
         patch("src.main.enviar_mensaje_telegram", side_effect=mock_enviar):

        await main.process_telegram_update(chat_id="12345", text="/tendencias", message_id="m1")

    # Debe haber enviado 2 mensajes: 1. "Analizando...", 2. Reporte de tendencias
    assert len(captured_messages) >= 2
    final_msg = captured_messages[-1]
    print("\nMensaje generado por /tendencias:\n", final_msg)

    # Verificaciones críticas:
    # 1. NO debe decir "No tienes suficientes gastos registrados el mes pasado"
    assert "No tienes suficientes gastos registrados" not in final_msg
    # 2. Debe comparar Septiembre vs Agosto
    assert "Septiembre vs Agosto" in final_msg
    # 3. Debe mostrar gasto bruto actual ($35,527 = 21456 + 14071)
    assert "$35,527" in final_msg
    # 4. Debe incluir la nota de que en los días 1 a 4 de Agosto no hubo registros
    assert "No registraste gastos entre el día 1 y 4 de Agosto" in final_msg
    # 5. Debe mostrar el cierre de Agosto ($281,929)
    assert "$281,929" in final_msg
    # 6. Debe incluir la nota transparente del reembolso de $180,000 en Salud
    assert "$180,000" in final_msg
    print("  ✅ /tendencias generó el reporte predictivo completo con fallback exitosamente.")


async def test_tendencias_first_month_notice():
    """Prueba el escenario de primer mes absoluto (sin datos el mes anterior)."""
    print("\n--- TEST: /tendencias primer mes absoluto ---")
    captured_messages = []

    async def mock_enviar(chat_id, text, **kwargs):
        captured_messages.append(text)

    mock_act_mtd = {
        "Alimentación": {"total": 20000.0, "gasto_bruto": 20000.0, "aportes": 0.0},
    }

    def mock_summary(offset, max_day=None):
        if offset == 0:
            return mock_act_mtd, 9, 2026
        return {}, 8, 2026

    with patch.object(main.sheets_client, "get_month_summary", side_effect=mock_summary), \
         patch("src.models.get_local_date", return_value=date(2026, 9, 4)), \
         patch("src.main.enviar_mensaje_telegram", side_effect=mock_enviar):

        await main.process_telegram_update(chat_id="12345", text="/tendencias", message_id="m2")

    final_msg = captured_messages[-1]
    assert "Aún no tienes gastos registrados en Agosto" in final_msg
    assert "$20,000" in final_msg
    print("  ✅ Mensaje amistoso de primer mes validado correctamente.")


async def run_all():
    test_parse_flexible_date()
    test_get_month_summary_includes_latin_dates()
    await test_tendencias_fallback_when_past_mtd_is_zero()
    await test_tendencias_first_month_notice()
    print("\n🎉 TODOS LOS TESTS DE TENDENCIAS Y FECHAS PASARON EXITOSAMENTE.")


if __name__ == "__main__":
    asyncio.run(run_all())
