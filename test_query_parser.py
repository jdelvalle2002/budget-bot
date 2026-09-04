"""Pruebas unitarias para el motor analítico semántico de consultas (NLQ) y lematización."""

import os
os.environ.setdefault("GEMINI_API_KEY", "mock-api-key")

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from src.parser import (
    AnalisisQuery,
    FiltroTiempo,
    IntentType,
    filtrar_transacciones,
    quitar_acentos,
    responder_consulta_natural,
)


def test_quitar_acentos():
    assert quitar_acentos("Telefonía") == "Telefonia"
    assert quitar_acentos("Telefonía").lower() == "telefonia"
    assert quitar_acentos("Almuerzo rápido") == "Almuerzo rapido"
    assert quitar_acentos("CAFÉ") == "CAFE"


def test_filtrar_transacciones_fechas_explicitas():
    txs = [
        {"fecha": "2026-09-01", "monto": 10000, "concepto": "Supermercado"},
        {"fecha": "2026-09-03", "monto": 5000, "concepto": "Almuerzo"},
        {"fecha": "2026-09-04", "monto": 7000, "concepto": "Almuerzo"},
        {"fecha": "2026-08-30", "monto": 12000, "concepto": "Cena"},
    ]
    # Rango explícito del 2 al 4 de septiembre
    res = filtrar_transacciones(
        txs,
        filtro_tiempo=FiltroTiempo.PERSONALIZADO,
        fecha_desde="2026-09-02",
        fecha_hasta="2026-09-04"
    )
    assert len(res) == 2
    conceptos = [tx["concepto"] for tx in res]
    assert "Almuerzo" in conceptos
    assert "Supermercado" not in conceptos
    assert "Cena" not in conceptos


def test_responder_consulta_natural_almorzando_matches_almuerzo():
    """Verifica que 'almorzando' encuentre registros titulados 'Almuerzo' usando lemas."""
    mock_txs = [
        {
            "fecha": "2026-09-02T13:00:00",
            "monto": "6500",
            "concepto": "Almuerzo",
            "categoria": "Alimentación",
            "tipo": "gasto",
            "metodo": "Débito",
            "comentarios": "Casino oficina"
        },
        {
            "fecha": "2026-09-03T13:30:00",
            "monto": "8000",
            "concepto": "Almuerzo",
            "categoria": "Alimentación",
            "tipo": "gasto",
            "metodo": "Débito",
            "comentarios": "Menu del dia"
        },
        {
            "fecha": "2026-09-01T20:00:00",
            "monto": "25000",
            "concepto": "Supermercado Lider",
            "categoria": "Alimentación",
            "tipo": "gasto",
            "metodo": "Crédito",
            "comentarios": "Compras de despensa"
        }
    ]

    mock_gemini_json = """{
        "intent": "busqueda_especifica",
        "filtro_tiempo": "este_mes",
        "fecha_desde": "2026-09-01",
        "fecha_hasta": "2026-09-30",
        "periodo_legible": "este mes",
        "categoria_objetivo": "Alimentación",
        "metodo_objetivo": null,
        "terminos_busqueda": ["almuerzo", "almorzar", "almorzando", "casino", "menu", "colacion"],
        "concepto_objetivo": "Almuerzo"
    }"""

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.chats.create.return_value = mock_chat
        mock_response = MagicMock()
        mock_response.text = mock_gemini_json
        mock_chat.send_message.return_value = mock_response

        respuesta = responder_consulta_natural("Cuanto he gastado almorzando este mes", mock_txs)
        print("\nRespuesta Almorzando -> Almuerzo:\n", respuesta)

        # Debe haber encontrado las 2 transacciones de Almuerzo (6.500 + 8.000 = 14.500)
        assert "14,500" in respuesta
        assert "2 veces" in respuesta
        assert "Almuerzo" in respuesta
        assert "Supermercado" not in respuesta


def test_responder_consulta_natural_mayor_gasto():
    """Verifica que el intent 'mayor_gasto' seleccione el desembolso más alto correctamente."""
    mock_txs = [
        {"fecha": "2026-09-01", "monto": "15000", "concepto": "Bencina Shell", "categoria": "Transporte", "tipo": "gasto", "metodo": "Débito"},
        {"fecha": "2026-09-02", "monto": "85000", "concepto": "Mantención Auto", "categoria": "Transporte", "tipo": "gasto", "metodo": "Crédito"},
        {"fecha": "2026-09-03", "monto": "12000", "concepto": "Uber", "categoria": "Transporte", "tipo": "gasto", "metodo": "Débito"},
    ]

    mock_gemini_json = """{
        "intent": "mayor_gasto",
        "filtro_tiempo": "este_mes",
        "fecha_desde": "2026-09-01",
        "fecha_hasta": "2026-09-30",
        "periodo_legible": "este mes",
        "categoria_objetivo": null,
        "metodo_objetivo": null,
        "terminos_busqueda": [],
        "concepto_objetivo": null
    }"""

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.chats.create.return_value = mock_chat
        mock_response = MagicMock()
        mock_response.text = mock_gemini_json
        mock_chat.send_message.return_value = mock_response

        respuesta = responder_consulta_natural("cuál fue mi gasto más alto este mes", mock_txs)
        print("\nRespuesta Mayor Gasto:\n", respuesta)

        assert "85,000" in respuesta
        assert "Mantención Auto" in respuesta


def test_responder_consulta_natural_cross_filtro_metodo():
    """Verifica que se filtre por método de pago cuando se especifica."""
    mock_txs = [
        {"fecha": "2026-09-01", "monto": "20000", "concepto": "Super", "categoria": "Alimentación", "tipo": "gasto", "metodo": "Planilla"},
        {"fecha": "2026-09-02", "monto": "35000", "concepto": "Super", "categoria": "Alimentación", "tipo": "gasto", "metodo": "Débito"},
    ]

    mock_gemini_json = """{
        "intent": "busqueda_especifica",
        "filtro_tiempo": "este_mes",
        "fecha_desde": "2026-09-01",
        "fecha_hasta": "2026-09-30",
        "periodo_legible": "este mes",
        "categoria_objetivo": null,
        "metodo_objetivo": "Planilla",
        "terminos_busqueda": ["super", "supermercado"],
        "concepto_objetivo": "Super"
    }"""

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_chat = MagicMock()
        mock_client.chats.create.return_value = mock_chat
        mock_response = MagicMock()
        mock_response.text = mock_gemini_json
        mock_chat.send_message.return_value = mock_response

        respuesta = responder_consulta_natural("cuanto gaste en super por planilla", mock_txs)
        print("\nRespuesta Filtro Planilla:\n", respuesta)

        assert "20,000" in respuesta
        assert "1 veces" in respuesta


if __name__ == "__main__":
    test_quitar_acentos()
    test_filtrar_transacciones_fechas_explicitas()
    test_responder_consulta_natural_almorzando_matches_almuerzo()
    test_responder_consulta_natural_mayor_gasto()
    test_responder_consulta_natural_cross_filtro_metodo()
    print("\n🎉 TODOS LOS TESTS DE QUERY PARSER PASARON EXITOSAMENTE.")
