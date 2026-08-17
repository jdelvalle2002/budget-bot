import os
import json
import logging
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from src.models import Transaction

logger = logging.getLogger(__name__)

class GoogleSheetsClient:
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

    def __init__(self):
        self.spreadsheet_id = os.getenv("GOOGLE_SHEET_ID")
        
        if not self.spreadsheet_id:
            raise ValueError("La variable de entorno GOOGLE_SHEET_ID debe estar configurada.")
            
        self.service = self._authenticate()
        self.sheet = self.service.spreadsheets()

    def _authenticate(self):
        """Autentica con la API de Google Sheets usando Service Account."""
        creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
        creds_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        
        if creds_json:
            # Si estamos en Render, leemos el JSON directamente de la variable de entorno
            creds_dict = json.loads(creds_json)
            creds = Credentials.from_service_account_info(
                creds_dict, scopes=self.SCOPES
            )
        elif creds_file:
            # Si estamos en local, leemos del archivo credentials.json
            creds = Credentials.from_service_account_file(
                creds_file, scopes=self.SCOPES
            )
        else:
            raise ValueError("No se encontraron credenciales de Google (ni archivo ni variable de entorno).")

        return build('sheets', 'v4', credentials=creds)

    def _get_existing_transaction_ids(self, range_name: str = "Gastos!A:A") -> set:
        """
        Lee la primera columna (ID_Transaccion) para garantizar idempotencia.
        Devuelve un conjunto con todos los IDs existentes.
        """
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()
            values = result.get('values', [])
            if not values:
                return set()
            return {row[0] for row in values if row}
        except Exception as e:
            logger.error(f"Error al leer IDs existentes: {e}")
            return set()

    def _find_row_index(self, id_transaccion: str, sheet_name: str = "Gastos") -> int:
        """Encuentra el índice de la fila (1-indexed) de una transacción."""
        range_name = f"{sheet_name}!A:A"
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()
            values = result.get('values', [])
            for i, row in enumerate(values):
                if row and row[0] == id_transaccion:
                    return i + 1 # 1-indexed
            return -1
        except Exception as e:
            logger.error(f"Error al buscar ID {id_transaccion}: {e}")
            return -1

    def _get_sheet_id(self, sheet_name: str) -> int:
        """Obtiene el ID interno numérico de una pestaña por su nombre."""
        try:
            spreadsheet = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
            for sheet in spreadsheet.get('sheets', []):
                if sheet.get('properties', {}).get('title') == sheet_name:
                    return sheet.get('properties', {}).get('sheetId')
            return 0
        except Exception as e:
            logger.error(f"Error obteniendo sheetId para {sheet_name}: {e}")
            return 0

    def append_transaction(self, transaction: Transaction, sheet_name: str = "Gastos") -> bool:
        """
        Inserta una transacción en Google Sheets si su ID no existe previamente.
        Retorna True si la inserción fue exitosa o si ya existía (idempotencia cumplida),
        False si hubo un error.
        """
        # Chequear Idempotencia
        range_name_ids = f"{sheet_name}!A:A"
        existing_ids = self._get_existing_transaction_ids(range_name_ids)
        
        if transaction.id_transaccion in existing_ids:
            logger.info(f"Transacción {transaction.id_transaccion} ya existe. Omitiendo (Idempotencia).")
            return True # Exitoso desde la perspectiva del flujo, no hay que re-hacer.

        # Insertar
        try:
            body = {
                'values': [transaction.to_row()]
            }
            range_name = f"{sheet_name}!A:H"
            self.sheet.values().append(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueInputOption='USER_ENTERED',
                body=body
            ).execute()
            
            logger.info(f"Transacción {transaction.id_transaccion} insertada correctamente.")
            return True
        except Exception as e:
            logger.error(f"Error al insertar en Google Sheets: {e}")
            return False

    def update_transaction(self, transaction: Transaction, sheet_name: str = "Gastos") -> bool:
        """Sobrescribe una transacción existente."""
        row_index = self._find_row_index(transaction.id_transaccion, sheet_name)
        if row_index == -1:
            logger.error(f"No se encontró la transacción {transaction.id_transaccion} para actualizar.")
            return False
            
        try:
            body = {'values': [transaction.to_row()]}
            range_name = f"{sheet_name}!A{row_index}:H{row_index}"
            self.sheet.values().update(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueInputOption='USER_ENTERED',
                body=body
            ).execute()
            logger.info(f"Transacción {transaction.id_transaccion} actualizada correctamente en fila {row_index}.")
            return True
        except Exception as e:
            logger.error(f"Error al actualizar la fila {row_index}: {e}")
            return False

    def delete_transaction(self, id_transaccion: str, sheet_name: str = "Gastos") -> bool:
        """Borra la fila de una transacción de forma definitiva."""
        row_index = self._find_row_index(id_transaccion, sheet_name)
        if row_index == -1:
            logger.error(f"No se encontró la transacción {id_transaccion} para borrar.")
            return False
            
        sheet_id = self._get_sheet_id(sheet_name)
        
        request = {
            "requests": [
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "startIndex": row_index - 1, # 0-indexed y exclusivo final
                            "endIndex": row_index
                        }
                    }
                }
            ]
        }
        try:
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body=request
            ).execute()
            logger.info(f"Transacción {id_transaccion} borrada (fila {row_index}).")
            return True
        except Exception as e:
            logger.error(f"Error borrando fila {row_index}: {e}")
            return False
