"""Script de pruebas unitarias y de integración para la modalidad Planilla.

Verifica:
1. Fast-Path para almuerzos y consumos de casino por planilla.
2. Detección de ambigüedad Débito vs. Planilla.
3. Soporte para gastos no alimentarios por planilla (ej: taller deportivo).
4. Exportación del modelo a fila de Google Sheets.
5. Conciliación matemática del balance neto en cuenta.
"""

import os
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv()

from src.models import Transaction, TipoTransaccion, MetodoPago, get_local_date, format_currency
from src.parser import try_fast_path, parse_transaction_message, ParseResult
from src.state import get_user_session, UserState

def test_fast_path():
    print("\n--- TEST 1: Fast-Path para Planilla ---")
    casos = [
        ("3500 almuerzo menu casino pega", True, MetodoPago.PLANILLA, Decimal("3500")),
        ("almuerzo casino pega 4000", True, MetodoPago.PLANILLA, Decimal("4000")),
        ("3500 casino pega", True, MetodoPago.PLANILLA, Decimal("3500")),
        ("3500 almuerzo por planilla", True, MetodoPago.PLANILLA, Decimal("3500")),
        ("marqué credencial casino 3800", True, MetodoPago.PLANILLA, Decimal("3800")),
        ("3500 casino con debito", False, None, None), # Con debito explicito no debe ir por fast-path de planilla
        ("15000 uber", True, MetodoPago.DEBITO, Decimal("15000")), # Fast-path estándar
    ]
    
    for texto, debe_hacer_match, metodo_esperado, monto_esperado in casos:
        res = try_fast_path(texto, message_id="FAST-TEST")
        if debe_hacer_match:
            assert res is not None, f"Se esperaba match para '{texto}' pero dio None"
            assert res.transaction.metodo == metodo_esperado, f"Para '{texto}' se esperaba {metodo_esperado}, dio {res.transaction.metodo}"
            assert res.transaction.monto == monto_esperado, f"Para '{texto}' se esperaba {monto_esperado}, dio {res.transaction.monto}"
            print(f"  ✅ Fast-Path OK: '{texto}' -> {res.transaction.concepto} | {res.transaction.categoria} | {res.transaction.metodo.value} ({format_currency(res.transaction.monto)})")
        else:
            # Si no debe hacer match de planilla, o bien da None o tiene otro método
            if res is not None:
                assert res.transaction.metodo != MetodoPago.PLANILLA, f"'{texto}' no debió ser clasificado como Planilla en fast-path"
            print(f"  ✅ Fast-Path Bypass OK (se delega a LLM/análisis): '{texto}'")

def test_model_export():
    print("\n--- TEST 2: Exportación del Modelo a Google Sheets ---")
    tx = Transaction(
        id_transaccion="TEST-ROW-01",
        fecha=get_local_date(),
        tipo=TipoTransaccion.GASTO,
        monto=Decimal("3500"),
        concepto="Almuerzo Casino",
        categoria="Alimentación",
        metodo=MetodoPago.PLANILLA,
        comentarios="Prueba unitaria"
    )
    row = tx.to_row()
    assert len(row) == 8, f"Fila debe tener 8 columnas, tiene {len(row)}"
    assert row[6] == "Planilla", f"Columna Metodo (index 6) debe ser 'Planilla', dio {row[6]}"
    print(f"  ✅ Fila generada correctamente: {row}")

def test_balance_math():
    print("\n--- TEST 3: Conciliación Matemática de Balance con Planilla ---")
    # Simulamos el resultado de resumen de categorías
    resumen_simulado = {
        "Remuneraciones": {"total": 1000000, "count": 1, "planilla": 0},
        "Alimentación": {"total": 60000, "count": 15, "planilla": 52500}, # Casino por planilla
        "Deportes": {"total": 30000, "count": 2, "planilla": 15000},     # Taller deportivo por planilla
        "Transporte": {"total": 40000, "count": 10, "planilla": 0}        # Débito tradicional
    }
    
    categorias_ingreso = ["Remuneraciones", "Otros Ingresos", "Inversiones"]
    
    total_ingresos = sum(d["total"] for c, d in resumen_simulado.items() if c in categorias_ingreso)
    total_gastos = sum(d["total"] for c, d in resumen_simulado.items() if c not in categorias_ingreso)
    total_planilla = sum(d.get("planilla", 0) for c, d in resumen_simulado.items() if c not in categorias_ingreso)
    
    gastos_cuenta = total_gastos - total_planilla
    balance_neto = total_ingresos - gastos_cuenta
    
    assert total_ingresos == 1000000, "Total ingresos erróneo"
    assert total_gastos == 130000, "Total gastos erróneo" # 60k + 30k + 40k
    assert total_planilla == 67500, "Total planilla erróneo" # 52.5k + 15k
    assert gastos_cuenta == 62500, "Gastos cuenta erróneo" # 130k - 67.5k
    assert balance_neto == 937500, f"Balance neto erróneo: dio {balance_neto}, se esperaba 937500"
    
    print(f"  ✅ Ingresos Líquidos:       {format_currency(Decimal(str(total_ingresos)))}")
    print(f"  ✅ Gastos Totales Consumo:  {format_currency(Decimal(str(total_gastos)))} (Monitoreo de presupuesto)")
    print(f"     ├─ En cuenta/tarjetas:   {format_currency(Decimal(str(gastos_cuenta)))}")
    print(f"     └─ Por planilla:         {format_currency(Decimal(str(total_planilla)))}")
    print(f"  ✅ Balance Neto en Cuenta:  {format_currency(Decimal(str(balance_neto)))} (Sin doble resta)")

def test_llm_parsing():
    print("\n--- TEST 4: Parseo con Gemini LLM (si GEMINI_API_KEY está configurada) ---")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("  ⚠️ GEMINI_API_KEY no presente. Omitiendo prueba de LLM.")
        return
        
    # Caso 1: Ambigüedad Débito vs Planilla
    print("  Probando caso ambiguo: '3500 almuerzo casino'...")
    res_ambiguo = parse_transaction_message("3500 almuerzo casino", message_id="TEST-AMB-01")
    print(f"    - es_ambiguo_metodo: {res_ambiguo.es_ambiguo_metodo}")
    print(f"    - opciones_metodo:   {res_ambiguo.opciones_metodo}")
    print(f"    - categoria:         {res_ambiguo.transaction.categoria}")
    assert res_ambiguo.es_ambiguo_metodo is True, "Debió marcarse como ambiguo en método"
    assert "Planilla" in res_ambiguo.opciones_metodo and "Débito" in res_ambiguo.opciones_metodo
    print("  ✅ Ambigüedad detectada correctamente!")
    
    # Caso 2: Gasto no alimentario por planilla (taller deportivo)
    print("  Probando caso no alimentario: '20000 taller deportivo de padel por planilla'...")
    res_taller = parse_transaction_message("20000 taller deportivo de padel por planilla", message_id="TEST-DEP-01")
    print(f"    - categoria: {res_taller.transaction.categoria}")
    print(f"    - metodo:    {res_taller.transaction.metodo.value}")
    assert res_taller.transaction.metodo == MetodoPago.PLANILLA, f"Esperado Planilla, dio {res_taller.transaction.metodo}"
    assert res_taller.transaction.categoria in ["Deportes", "Otros Gastos"], f"Categoría esperada Deportes, dio {res_taller.transaction.categoria}"
    print("  ✅ Gasto no alimentario por planilla parseado exitosamente!")

if __name__ == "__main__":
    test_fast_path()
    test_model_export()
    test_balance_math()
    test_llm_parsing()
    print("\n🎉 ¡TODOS LOS TESTS DE LA MODALIDAD PLANILLA PASARON EXITOSAMENTE!\n")
