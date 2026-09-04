"""Pruebas de validación para la lógica de gastos brutos, aportes y gráficos de resumen y barras."""

import asyncio
import os
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock
from dotenv import load_dotenv

load_dotenv()

from src.models import format_currency, get_local_date
from src.sheets_client import GoogleSheetsClient
from src import main

def test_currency_formatting():
    print("\n--- TEST: Formato de Moneda ---")
    pos = Decimal("25000")
    neg = Decimal("-20000")
    zero = Decimal("0")
    assert format_currency(pos) == "$25,000", f"Esperado $25,000, obtenido {format_currency(pos)}"
    assert format_currency(neg) == "-$20,000", f"Esperado -$20,000, obtenido {format_currency(neg)}"
    assert format_currency(zero) == "$0", f"Esperado $0, obtenido {format_currency(zero)}"
    print("  ✅ Formato de moneda positivo, negativo y cero validado exitosamente.")

def test_sheets_client_summary_logic():
    print("\n--- TEST: Agrupación de Gasto Bruto y Aportes en get_month_summary ---")
    client = GoogleSheetsClient.__new__(GoogleSheetsClient)
    client.spreadsheet_id = "mock_sheet_id"
    
    # Mock sheet values
    mock_values = [
        ["ID", "Fecha", "Tipo", "Monto", "Concepto", "Categoría", "Método", "Comentarios"],
        # Gasto bruto Alimentación: 30000
        ["1", "2026-09-01", "Gasto", "30000", "Super", "Alimentación", "Débito", ""],
        # Aporte / Reembolso Alimentación: 50000
        ["2", "2026-09-02", "Ingreso", "50000", "Reembolso", "Alimentación", "Transferencia", ""],
        # Gasto Transporte: 15000
        ["3", "2026-09-03", "Gasto", "15000", "Metro", "Transporte", "Débito", ""],
        # Ingreso puro Sueldo: 500000
        ["4", "2026-09-01", "Ingreso", "500000", "Sueldo", "Remuneraciones", "Transferencia", ""],
    ]
    
    mock_get = MagicMock()
    mock_get.execute.return_value = {"values": mock_values}
    mock_values_resource = MagicMock()
    mock_values_resource.get.return_value = mock_get
    client.sheet = MagicMock()
    client.sheet.values.return_value = mock_values_resource
    
    res, t_month, t_year = client.get_month_summary(month_offset=0)
    
    alim = res["Alimentación"]
    trans = res["Transporte"]
    
    assert alim["gasto_bruto"] == 30000.0, f"Gasto bruto esperado 30000, obtenido {alim['gasto_bruto']}"
    assert alim["aportes"] == 50000.0, f"Aportes esperados 50000, obtenido {alim['aportes']}"
    assert alim["total"] == -20000.0, f"Neto esperado -20000, obtenido {alim['total']}"
    
    assert trans["gasto_bruto"] == 15000.0, f"Transporte bruto esperado 15000, obtenido {trans['gasto_bruto']}"
    assert trans["aportes"] == 0.0, f"Transporte aportes esperados 0, obtenido {trans['aportes']}"
    assert trans["total"] == 15000.0, f"Transporte neto esperado 15000, obtenido {trans['total']}"
    
    print("  ✅ sheets_client.get_month_summary computa gasto_bruto, aportes y neto correctamente.")

async def test_resumen_mensual_y_grafico_pie():
    print("\n--- TEST: Resumen Mensual (Pie Chart con Gasto Bruto y Desglose Transparente) ---")
    
    mock_resumen = {
        "Alimentación": {
            "total": -20000.0,
            "gasto_bruto": 30000.0,
            "aportes": 50000.0,
            "count": 2,
            "planilla": 0.0
        },
        "Transporte": {
            "total": 15000.0,
            "gasto_bruto": 15000.0,
            "aportes": 0.0,
            "count": 1,
            "planilla": 0.0
        },
        "Remuneraciones": {
            "total": 500000.0,
            "gasto_bruto": 0.0,
            "aportes": 0.0,
            "count": 1,
            "planilla": 0.0
        }
    }
    
    mock_categories_config = {
        "Alimentación": {"color": "#E74C3C", "presupuesto": 40000.0},
        "Transporte": {"color": "#3498DB", "presupuesto": 25000.0}
    }
    
    captured_messages = []
    captured_photos = []
    
    async def mock_enviar_mensaje(chat_id, text, parse_mode="Markdown", reply_markup=None):
        captured_messages.append({"text": text, "reply_markup": reply_markup})
        
    async def mock_enviar_foto(chat_id, photo_buf, caption="", parse_mode="Markdown", reply_markup=None):
        captured_photos.append({"photo_buf": photo_buf, "caption": caption, "reply_markup": reply_markup})

    with patch.object(main.sheets_client, "get_month_summary", return_value=(mock_resumen, 9, 2026)), \
         patch.object(main.sheets_client, "load_categories_from_config", return_value=mock_categories_config), \
         patch.object(main, "enviar_mensaje_telegram", side_effect=mock_enviar_mensaje), \
         patch.object(main, "enviar_foto_telegram", side_effect=mock_enviar_foto):
        
        await main.process_telegram_update(chat_id="12345", text="/resumen", message_id="1")
        
        assert len(captured_photos) == 1, "Debe enviar 1 foto (gráfico de torta)"
        photo = captured_photos[0]
        caption = photo["caption"]
        markup = photo["reply_markup"]
        
        # Verificar desglose transparente en caption
        assert "Alimentación" in caption, f"Alimentación debe estar en el caption: {caption}"
        assert "Aporte: -$50,000 ➔ Saldo a favor: +$20,000" in caption, f"No se encontró desglose esperado en caption: {caption}"
        assert "*Total Gastos Consumidos:* $45,000" in caption, f"Total consumido no coincide: {caption}"
        assert "*Reembolsos/Aportes:* -$50,000" in caption, f"Aportes en pie no coinciden: {caption}"
        assert "*Gasto Neto:* -$5,000 (a favor)" in caption, f"Gasto neto no coincide: {caption}"
        
        # Verificar inline button para gráfico de barras
        assert markup is not None, "Debe tener reply_markup con botón"
        assert markup["inline_keyboard"][0][0]["callback_data"] == "chart_bar:0"
        
        print("  ✅ Gráfico de torta y caption transparente validados con éxito.")

async def test_grafico_barras():
    print("\n--- TEST: Gráfico de Barras Horizontal (Neto) ---")
    
    mock_resumen = {
        "Alimentación": {
            "total": -20000.0,
            "gasto_bruto": 30000.0,
            "aportes": 50000.0,
            "count": 2,
            "planilla": 0.0
        },
        "Transporte": {
            "total": 15000.0,
            "gasto_bruto": 15000.0,
            "aportes": 0.0,
            "count": 1,
            "planilla": 0.0
        },
        "Remuneraciones": {
            "total": 500000.0,
            "gasto_bruto": 0.0,
            "aportes": 0.0,
            "count": 1,
            "planilla": 0.0
        }
    }
    
    captured_photos = []
    
    async def mock_enviar_foto(chat_id, photo_buf, caption="", parse_mode="Markdown", reply_markup=None):
        captured_photos.append({"photo_buf": photo_buf, "caption": caption, "reply_markup": reply_markup})

    with patch.object(main.sheets_client, "get_month_summary", return_value=(mock_resumen, 9, 2026)), \
         patch.object(main, "enviar_foto_telegram", side_effect=mock_enviar_foto):
        
        # Test direct helper
        await main.generar_y_enviar_grafico_barras(chat_id="12345", month_offset=0)
        
        # Test callback query dispatch
        await main.process_telegram_callback(chat_id="12345", callback_data="chart_bar:0")
        
        assert len(captured_photos) == 2, "Debe enviar 2 fotos (directa + callback)"
        photo = captured_photos[0]
        assert "Balance Neto por Categoría" in photo["caption"]
        assert "Rojo" in photo["caption"]
        assert "Verde" in photo["caption"]
        
        print("  ✅ Gráfico de barras horizontal generado y enviado correctamente.")

async def run_all():
    test_currency_formatting()
    test_sheets_client_summary_logic()
    await test_resumen_mensual_y_grafico_pie()
    await test_grafico_barras()
    print("\n🎉 TODOS LOS TESTS PASARON EXITOSAMENTE.")

if __name__ == "__main__":
    asyncio.run(run_all())
