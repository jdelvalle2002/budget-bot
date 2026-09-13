"""
Pruebas de compatibilidad para internacionalización (i18n), multimoneda y formato regional.
Verifica:
1. Formato por defecto (Chile, CLP, 0 decimales, prefijo, coma para miles).
2. Formato para Europa / Bélgica (EUR, 2 decimales, sufijo, coma decimal, punto miles).
3. Fast-Path con decimales y símbolos de moneda.
4. Cálculo de proyecciones en pacing con decimales.
5. Parseo de presupuestos con símbolos y decimales.
"""

import os
from decimal import Decimal
from datetime import date
from src.models import format_currency
from src.parser import try_fast_path
from src.pacing import compute_category_pacing
from src.main import parse_budget_amount

def test_default_chilean_formatting():
    # Limpiar variables a defaults
    os.environ["CURRENCY_SYMBOL"] = "$"
    os.environ["CURRENCY_DECIMALS"] = "0"
    os.environ["CURRENCY_POSITION"] = "prefix"
    os.environ["CURRENCY_DECIMAL_SEP"] = "."
    os.environ["CURRENCY_THOUSANDS_SEP"] = ","

    assert format_currency(Decimal("25000")) == "$25,000"
    assert format_currency(Decimal("-20000")) == "-$20,000"
    assert format_currency(Decimal("0")) == "$0"
    assert format_currency(Decimal("1500000")) == "$1,500,000"
    print("✅ test_default_chilean_formatting superado.")

def test_european_belgian_formatting():
    os.environ["CURRENCY_SYMBOL"] = "€"
    os.environ["CURRENCY_DECIMALS"] = "2"
    os.environ["CURRENCY_POSITION"] = "suffix"
    os.environ["CURRENCY_DECIMAL_SEP"] = ","
    os.environ["CURRENCY_THOUSANDS_SEP"] = "."

    assert format_currency(Decimal("25.50")) == "25,50 €"
    assert format_currency(Decimal("-20.00")) == "-20,00 €"
    assert format_currency(Decimal("-5.5")) == "-5,50 €"
    assert format_currency(Decimal("1234.56")) == "1.234,56 €"
    assert format_currency(Decimal("0")) == "0,00 €"
    print("✅ test_european_belgian_formatting superado.")

def test_prefix_european_formatting():
    os.environ["CURRENCY_SYMBOL"] = "€"
    os.environ["CURRENCY_DECIMALS"] = "2"
    os.environ["CURRENCY_POSITION"] = "prefix"
    os.environ["CURRENCY_DECIMAL_SEP"] = "."
    os.environ["CURRENCY_THOUSANDS_SEP"] = ","

    assert format_currency(Decimal("25.50")) == "€25.50"
    assert format_currency(Decimal("-20.00")) == "-€20.00"
    print("✅ test_prefix_european_formatting superado.")

def test_fast_path_with_decimals():
    # Probar que try_fast_path reconoce decimales con punto y coma
    res1 = try_fast_path("15000 uber", "msg-1")
    assert res1 is not None
    assert res1.transaction.monto == Decimal("15000")

    res2 = try_fast_path("12.50 almuerzo", "msg-2")
    assert res2 is not None
    assert res2.transaction.monto == Decimal("12.50")
    assert res2.transaction.concepto == "Almuerzo"

    res3 = try_fast_path("12,50 almuerzo", "msg-3")
    assert res3 is not None
    assert res3.transaction.monto == Decimal("12.50")

    res4 = try_fast_path("€12.50 almuerzo", "msg-4")
    assert res4 is not None
    assert res4.transaction.monto == Decimal("12.50")

    res5 = try_fast_path("almuerzo 12.50", "msg-5")
    assert res5 is not None
    assert res5.transaction.monto == Decimal("12.50")

    print("✅ test_fast_path_with_decimals superado.")

def test_pacing_with_decimals():
    os.environ["CURRENCY_DECIMALS"] = "2"
    # Día 15 de un mes de 30 días (tau = 0.5)
    target = date(2026, 4, 15)
    # Gasto de 15.25 € -> Proyección = 15.25 / 0.5 = 30.50 €
    m = compute_category_pacing(
        categoria="Alimentación",
        limite=Decimal("100.00"),
        gasto_neto=Decimal("15.25"),
        target_date=target
    )
    assert m is not None
    assert m.proyeccion_fin_mes == Decimal("30.50")

    # Restaurar
    os.environ["CURRENCY_DECIMALS"] = "0"
    m_clp = compute_category_pacing(
        categoria="Alimentación",
        limite=Decimal("100000"),
        gasto_neto=Decimal("15250"),
        target_date=target
    )
    assert m_clp is not None
    assert m_clp.proyeccion_fin_mes == Decimal("30500")
    print("✅ test_pacing_with_decimals superado.")

def test_parse_budget_amount_multicurrency():
    # Enteros estándar
    assert parse_budget_amount("80k") == Decimal("80000")
    assert parse_budget_amount("$150000") == Decimal("150000")
    assert parse_budget_amount("150.000") == Decimal("150000")

    # Decimales europeos / símbolos
    assert parse_budget_amount("€150.50") == Decimal("150.50")
    assert parse_budget_amount("150,50 €") == Decimal("150.50")
    assert parse_budget_amount("150,50") == Decimal("150.50")
    assert parse_budget_amount("0") is None
    assert parse_budget_amount("ninguno") is None
    print("✅ test_parse_budget_amount_multicurrency superado.")

if __name__ == "__main__":
    test_default_chilean_formatting()
    test_european_belgian_formatting()
    test_prefix_european_formatting()
    test_fast_path_with_decimals()
    test_pacing_with_decimals()
    test_parse_budget_amount_multicurrency()
    print("\n🎉 ¡TODAS LAS PRUEBAS DE i18n Y MULTIMONEDA PASARON EXITOSAMENTE!")
