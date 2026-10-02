"""Pruebas para cambio directo de categoría, directiva CATEGORIA xxxxx y neteo de ajustes/reembolsos."""

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch, AsyncMock

from src.models import TipoTransaccion, MetodoPago
from src.parser import extraer_categoria_explicita, try_fast_path, parse_transaction_message
from src.sheets_client import GoogleSheetsClient
from src.state import get_user_session, UserState
from src import main


CATEGORIAS_TEST = [
    "Alimentación", "Transporte", "Salidas", "Hogar", "Cuentas Básicas", 
    "Salud", "Educación", "Otros Gastos", "Remuneraciones", "Otros Ingresos", "Inversiones"
]


def test_extraer_categoria_explicita():
    # 1. Con dos puntos
    assert extraer_categoria_explicita("15000 uber cat: transporte", CATEGORIAS_TEST) == "Transporte"
    assert extraer_categoria_explicita("25000 super categoría: alimentación", CATEGORIAS_TEST) == "Alimentación"
    assert extraer_categoria_explicita("CATEGORIA: Hogar 12000 cloro", CATEGORIAS_TEST) == "Hogar"
    
    # 2. Sin dos puntos
    assert extraer_categoria_explicita("30000 zapatillas categoria otros gastos", CATEGORIAS_TEST) == "Otros Gastos"
    assert extraer_categoria_explicita("10000 bar cat salidas", CATEGORIAS_TEST) == "Salidas"
    assert extraer_categoria_explicita("CATEGORIA Alimentacion me transfirieron 28k", CATEGORIAS_TEST) == "Alimentación"

    # 3. Mayúsculas / Minúsculas / Acentos
    assert extraer_categoria_explicita("5000 remedios cat: salud", CATEGORIAS_TEST) == "Salud"
    assert extraer_categoria_explicita("cat: cuentas basicas 20000 luz", CATEGORIAS_TEST) == "Cuentas Básicas"

    # 4. Caso negativo
    assert extraer_categoria_explicita("15000 uber al centro", CATEGORIAS_TEST) is None


def test_try_fast_path_categoria_explicita():
    res = try_fast_path("15000 uber cat: transporte", message_id="FAST-CAT-1", categorias_disponibles=CATEGORIAS_TEST)
    assert res is not None
    assert res.transaction.monto == Decimal("15000")
    assert res.transaction.categoria == "Transporte"
    assert res.transaction.concepto == "Uber"
    assert res.transaction.tipo == TipoTransaccion.GASTO

    res2 = try_fast_path("25000 super lider categoria alimentacion", message_id="FAST-CAT-2", categorias_disponibles=CATEGORIAS_TEST)
    assert res2 is not None
    assert res2.transaction.monto == Decimal("25000")
    assert res2.transaction.categoria == "Alimentación"


def test_update_transaction_category_sheets_client():
    client = GoogleSheetsClient.__new__(GoogleSheetsClient)
    client.spreadsheet_id = "test_sheet"
    client._find_row_index = MagicMock(return_value=12)
    client.sheet = MagicMock()
    mock_update = MagicMock()
    mock_update.execute.return_value = {}
    mock_values = MagicMock()
    mock_values.update.return_value = mock_update
    client.sheet.values.return_value = mock_values

    success = client.update_transaction_category(id_transaccion="TX-123", nueva_categoria="Alimentación", sheet_name="2026")
    assert success is True
    client.sheet.values().update.assert_called_with(
        spreadsheetId="test_sheet",
        range="2026!F12",
        valueInputOption="USER_ENTERED",
        body={"values": [["Alimentación"]]}
    )


@pytest.mark.asyncio
async def test_telegram_callback_edit_cat_flow():
    captured_messages = []

    async def mock_enviar(chat_id, text, reply_markup=None, **kwargs):
        captured_messages.append({"text": text, "reply_markup": reply_markup})

    with patch.object(main, "enviar_mensaje_telegram", side_effect=mock_enviar), \
         patch.object(main.sheets_client, "load_categories_from_config", return_value={"Alimentación": {}, "Hogar": {}, "Salidas": {}}), \
         patch.object(main.sheets_client, "update_transaction_category", return_value=True):

        # 1. Usuario toca "✏️ Editar"
        await main.process_telegram_callback(chat_id="999", callback_data="edit:TX-ROOMIE-1")
        assert len(captured_messages) == 1
        markup = captured_messages[-1]["reply_markup"]
        btn_data = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
        assert "edit_cat:TX-ROOMIE-1" in btn_data

        # 2. Usuario toca "🏷️ Cambiar Categoría"
        captured_messages.clear()
        await main.process_telegram_callback(chat_id="999", callback_data="edit_cat:TX-ROOMIE-1")
        assert len(captured_messages) == 1
        cat_markup = captured_messages[-1]["reply_markup"]
        cat_callbacks = [btn["callback_data"] for row in cat_markup["inline_keyboard"] for btn in row]
        assert "set_cat:TX-ROOMIE-1:Alimentación" in cat_callbacks
        assert "set_cat:TX-ROOMIE-1:Hogar" in cat_callbacks

        # 3. Usuario selecciona "Alimentación"
        captured_messages.clear()
        await main.process_telegram_callback(chat_id="999", callback_data="set_cat:TX-ROOMIE-1:Alimentación")
        assert len(captured_messages) == 1
        assert "Categoría actualizada exitosamente a *Alimentación*" in captured_messages[0]["text"]

        session = get_user_session(999)
        assert session.state == UserState.IDLE
        assert session.edit_transaction_id is None


@pytest.mark.asyncio
async def test_awaiting_edit_direct_category_text():
    captured_messages = []

    async def mock_enviar(chat_id, text, reply_markup=None, **kwargs):
        captured_messages.append({"text": text, "reply_markup": reply_markup})

    session = get_user_session(888)
    session.state = UserState.AWAITING_EDIT
    session.edit_transaction_id = "TX-EDIT-DIRECT"

    with patch.object(main, "enviar_mensaje_telegram", side_effect=mock_enviar), \
         patch.object(main.sheets_client, "load_categories_from_config", return_value={"Alimentación": {}, "Hogar": {}}), \
         patch.object(main.sheets_client, "update_transaction_category", return_value=True) as mock_update:

        # Usuario escribe directamente "CATEGORIA Alimentación" en modo edición
        await main.process_telegram_update(chat_id="888", text="CATEGORIA Alimentación", message_id="msg-1")
        mock_update.assert_called_with("TX-EDIT-DIRECT", "Alimentación")
        assert "Categoría actualizada exitosamente a *Alimentación*" in captured_messages[-1]["text"]
        assert session.state == UserState.IDLE
