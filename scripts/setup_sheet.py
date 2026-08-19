import os
import sys
import argparse
import logging
from dotenv import load_dotenv

# Asegurar que podemos importar desde src
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sheets_client import GoogleSheetsClient
from src.models import get_local_date

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_CATEGORIES = [
    ["Alimentación", "#E74C3C", ""],
    ["Deportes", "#3498DB", ""],
    ["Hogar", "#2ECC71", ""],
    ["Inversiones", "#F39C12", ""],
    ["Mesada", "#9B59B6", ""],
    ["Salidas", "#E84393", ""],
    ["Salud", "#16A085", ""],
    ["Telefonía", "#1ABC9C", ""],
    ["Transporte", "#F1C40F", ""],
    ["Remuneraciones", "#27AE60", ""],
    ["Otros Gastos", "#7F8C8D", ""],
    ["Otros Ingresos", "#D35400", ""]
]

def setup_sheet(sheet_id: str):
    logger.info(f"Iniciando configuración para la planilla: {sheet_id}")
    
    # Temporalmente seteamos el ID en el entorno para instanciar el cliente
    os.environ["GOOGLE_SHEET_ID"] = sheet_id
    
    try:
        client = GoogleSheetsClient()
    except Exception as e:
        logger.error(f"Error autenticando con Google Sheets: {e}")
        return
        
    service = client.service
    hoy = get_local_date()
    current_year_str = str(hoy.year)
    
    # 1. Obtener pestañas actuales
    spreadsheet = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheets = spreadsheet.get('sheets', [])
    sheet_titles = [s.get('properties', {}).get('title') for s in sheets]
    
    requests = []
    
    # 2. Asegurar que existe la pestaña 'Config'
    if 'Config' not in sheet_titles:
        logger.info("Creando pestaña 'Config'...")
        requests.append({
            "addSheet": {
                "properties": {
                    "title": "Config",
                    "gridProperties": {"rowCount": 100, "columnCount": 10}
                }
            }
        })
        
    # 3. Asegurar que existe la pestaña del año actual
    if current_year_str not in sheet_titles:
        logger.info(f"Creando pestaña '{current_year_str}'...")
        requests.append({
            "addSheet": {
                "properties": {
                    "title": current_year_str,
                    "gridProperties": {"rowCount": 1000, "columnCount": 15}
                }
            }
        })
        
    # Ejecutar creación de pestañas si hay
    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": requests}
        ).execute()
        
    # Volver a obtener la info de las pestañas para conseguir los IDs numéricos
    spreadsheet = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheets = spreadsheet.get('sheets', [])
    sheet_ids = {s.get('properties', {}).get('title'): s.get('properties', {}).get('sheetId') for s in sheets}
    
    config_sheet_id = sheet_ids['Config']
    year_sheet_id = sheet_ids[current_year_str]
    
    # 4. Poblar 'Config'
    logger.info("Poblando 'Config'...")
    config_values = [["Categoría", "Color Hex (Gráficos)", "Presupuesto"]] + DEFAULT_CATEGORIES
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range="Config!A1:C",
        valueInputOption="USER_ENTERED",
        body={"values": config_values}
    ).execute()
    
    # 5. Formatear pestaña del año actual (Headers y validación)
    logger.info(f"Formateando pestaña '{current_year_str}'...")
    headers = [["ID_Transaccion", "Fecha", "Tipo", "Monto", "Concepto", "Categoría", "Metodo", "Comentarios"]]
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{current_year_str}!A1:H1",
        valueInputOption="USER_ENTERED",
        body={"values": headers}
    ).execute()
    
    # Batch Update para validación de datos (Dropdowns) y congelar fila 1
    batch_requests = []
    
    # Congelar fila 1 en pestaña de año
    batch_requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": year_sheet_id,
                "gridProperties": {"frozenRowCount": 1}
            },
            "fields": "gridProperties.frozenRowCount"
        }
    })
    
    # Configurar Data Validation para Categorías (Columna F -> Index 5)
    # Validar leyendo desde Config!A2:A
    batch_requests.append({
        "setDataValidation": {
            "range": {
                "sheetId": year_sheet_id,
                "startRowIndex": 1,
                "startColumnIndex": 5, # F
                "endColumnIndex": 6
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_RANGE",
                    "values": [{"userEnteredValue": "=Config!$A$2:$A"}]
                },
                "showCustomUi": True,
                "strict": False
            }
        }
    })
    
    # Configurar Data Validation para Tipo (Columna C -> Index 2)
    batch_requests.append({
        "setDataValidation": {
            "range": {
                "sheetId": year_sheet_id,
                "startRowIndex": 1,
                "startColumnIndex": 2, # C
                "endColumnIndex": 3
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": "Gasto"}, {"userEnteredValue": "Ingreso"}]
                },
                "showCustomUi": True,
                "strict": True
            }
        }
    })
    
    if batch_requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": batch_requests}
        ).execute()

    logger.info("✅ ¡Planilla configurada exitosamente!")
    logger.info("--------------------------------------------------")
    logger.info("👉 Para habilitar la AUTOGENERACIÓN de IDs manuales en Google Sheets:")
    logger.info("1. Abre tu planilla en el navegador.")
    logger.info("2. Ve a 'Extensiones' > 'Apps Script'.")
    logger.info("3. Pega este código:")
    logger.info(r"""
function onEdit(e) {
  if (!e) return; // Previene error si se ejecuta manualmente desde el editor
  var sheet = e.source.getActiveSheet();
  // Ignorar si no estamos en una hoja que parezca un año (ej. 2026)
  if (!sheet.getName().match(/^20\d{2}$/)) return;
  
  var range = e.range;
  // Si editan la columna B (Fecha, indice 2) y la columna A está vacía
  if (range.getColumn() === 2 && sheet.getRange(range.getRow(), 1).getValue() === "") {
    sheet.getRange(range.getRow(), 1).setValue("MANUAL-" + new Date().getTime());
  }
}
    """)
    logger.info("4. Guarda y cierra Apps Script (NO le des al botón Ejecutar/Play).")
    logger.info("   -> La función corre sola cuando editas la planilla directamente.")
    logger.info("--------------------------------------------------")


if __name__ == "__main__":
    load_dotenv()
    
    parser = argparse.ArgumentParser(description="Configura una planilla de Google Sheets vacía para budget-bot.")
    parser.add_argument("sheet_id", help="El ID de la Google Sheet (lo sacas de la URL)")
    
    args = parser.parse_args()
    setup_sheet(args.sheet_id)
