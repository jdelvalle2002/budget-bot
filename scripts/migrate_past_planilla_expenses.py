"""Script de utilidad para migrar/actualizar gastos de casino o almuerzos laborales pasados.

Permite buscar en Google Sheets aquellas transacciones que fueron registradas previamente
con método 'Débito' u 'Otro' y actualizarlas al nuevo método 'Planilla'.

Uso:
    # Modo simulación (no modifica la planilla, solo lista los candidatos):
    python scripts/migrate_past_planilla_expenses.py --dry-run

    # Modo aplicación (aplica los cambios en Google Sheets previa confirmación):
    python scripts/migrate_past_planilla_expenses.py --apply
"""

import os
import sys
import argparse
import logging
from decimal import Decimal
from dotenv import load_dotenv

# Asegurar importación de módulos internos con ruta relativa al proyecto
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sheets_client import GoogleSheetsClient
from src.models import get_local_date, format_currency

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

KEYWORDS_PLANILLA_DEFECTO = ["casino", "credencial", "almuerzo casino", "menu casino", "menú casino"]

def buscar_candidatos_a_planilla(client: GoogleSheetsClient, sheet_name: str, keywords: list[str]) -> list[dict]:
    """Busca filas en la hoja indicada que coincidan con keywords de casino y no sean Planilla."""
    range_name = f"{sheet_name}!A:H"
    try:
        result = client.sheet.values().get(
            spreadsheetId=client.spreadsheet_id,
            range=range_name
        ).execute()
        values = result.get('values', [])
    except Exception as e:
        logger.error(f"Error consultando la hoja '{sheet_name}': {e}")
        return []

    if not values or len(values) <= 1:
        return []

    candidatos = []
    # Fila 0 es encabezado: [ID_Transaccion, Fecha, Tipo, Monto, Concepto, Categoría, Metodo, Comentarios]
    for idx, row in enumerate(values[1:], start=2): # idx es 1-indexed en Sheets
        if len(row) >= 5:
            concepto = str(row[4]).lower() if len(row) > 4 else ""
            metodo = str(row[6]).strip() if len(row) > 6 else ""
            comentarios = str(row[7]).lower() if len(row) > 7 else ""
            
            # Verificar si coincide con palabras clave
            texto_busqueda = f"{concepto} {comentarios}"
            coincide = any(kw in texto_busqueda for kw in keywords)
            
            # Solo consideramos candidato si coincide y aún no es Planilla
            if coincide and metodo.lower() != "planilla":
                monto_raw = str(row[3]).replace(',', '').replace('$', '').strip()
                try:
                    monto = Decimal(monto_raw)
                except Exception:
                    monto = Decimal("0")
                    
                candidatos.append({
                    "row_index": idx,
                    "id": row[0] if len(row) > 0 else "",
                    "fecha": row[1] if len(row) > 1 else "",
                    "tipo": row[2] if len(row) > 2 else "",
                    "monto": monto,
                    "concepto": row[4] if len(row) > 4 else "",
                    "categoria": row[5] if len(row) > 5 else "",
                    "metodo_actual": metodo or "Vacío",
                })
    return candidatos

def actualizar_filas_a_planilla(client: GoogleSheetsClient, sheet_name: str, candidatos: list[dict]) -> int:
    """Actualiza la columna G (Metodo) de las filas seleccionadas a 'Planilla'."""
    actualizados = 0
    for item in candidatos:
        row_idx = item["row_index"]
        range_target = f"{sheet_name}!G{row_idx}"
        try:
            body = {'values': [["Planilla"]]}
            client.sheet.values().update(
                spreadsheetId=client.spreadsheet_id,
                range=range_target,
                valueInputOption="USER_ENTERED",
                body=body
            ).execute()
            actualizados += 1
            logger.info(f"Fila {row_idx} ({item['fecha']} - {item['concepto']}) actualizada a 'Planilla'.")
        except Exception as e:
            logger.error(f"Error actualizando fila {row_idx}: {e}")
    return actualizados

def main():
    load_dotenv()
    
    parser = argparse.ArgumentParser(
        description="Migra gastos pasados de casino o liquidación al método 'Planilla' en Google Sheets."
    )
    parser.add_argument(
        "--year", "-y",
        type=str,
        default=None,
        help="Año / nombre de la pestaña a revisar (default: año actual)."
    )
    parser.add_argument(
        "--keywords", "-k",
        nargs="+",
        default=KEYWORDS_PLANILLA_DEFECTO,
        help="Palabras clave para identificar los gastos a migrar (default: casino, credencial, almuerzo casino...)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica los cambios en Google Sheets. Si no se incluye, corre en modo seguro (dry-run)."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Omite la confirmación interactiva al aplicar los cambios."
    )

    args = parser.parse_args()
    sheet_name = args.year if args.year else str(get_local_date().year)
    
    try:
        client = GoogleSheetsClient()
    except Exception as e:
        logger.error(f"No se pudo inicializar GoogleSheetsClient: {e}")
        sys.exit(1)

    print("\n" + "=" * 65)
    print(f"  🏢 MIGRACIÓN DE GASTOS A 'PLANILLA' (Hoja: {sheet_name})")
    print("=" * 65)
    print(f"Keywords de búsqueda: {args.keywords}")
    print(f"Modo: {'⚠️ APLICAR CAMBIOS EN VIVO' if args.apply else '🔍 DRY-RUN (Solo consulta)'}")
    print("-" * 65)

    candidatos = buscar_candidatos_a_planilla(client, sheet_name, args.keywords)

    if not candidatos:
        print(f"\n✨ No se encontraron transacciones pendientes de migrar en la hoja '{sheet_name}'.")
        print("Todo parece estar al día con 'Planilla'.\n")
        return

    print(f"\nSe encontraron {len(candidatos)} transacciones candidatas a migrar:\n")
    total_monto = Decimal("0")
    for c in candidatos:
        total_monto += c["monto"]
        print(f"  • Fila {c['row_index']:3d} | {c['fecha']} | {format_currency(c['monto']):>10} | {c['concepto']:<20} | Método actual: {c['metodo_actual']}")

    print(f"\nTotal acumulado a reclasificar: {format_currency(total_monto)}")

    if not args.apply:
        print("\nℹ️ Para ejecutar la actualización en tu planilla de Google Sheets, vuelve a correr:")
        print(f"   python scripts/migrate_past_planilla_expenses.py --apply\n")
    else:
        if args.yes:
            confirm = "si"
        else:
            print("\n¿Deseas aplicar estos cambios a Google Sheets ahora?")
            try:
                confirm = input("Escribe 'si' para confirmar: ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print("\nOperación cancelada.")
                return

        if confirm in ["si", "sí", "yes", "y"]:
            print("\nActualizando transacciones en Google Sheets...")
            actualizados = actualizar_filas_a_planilla(client, sheet_name, candidatos)
            print(f"\n✅ ¡Migración completada! Se actualizaron {actualizados} registros a 'Planilla'.\n")
        else:
            print("\nOperación cancelada por el usuario. No se modificó ningún dato.\n")

if __name__ == "__main__":
    main()
