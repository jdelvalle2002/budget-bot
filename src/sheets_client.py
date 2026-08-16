import os
import logging
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from src.models import Transaction

logger = logging.getLogger(__name__)

class GoogleSheetsClient:
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

    def __init__(self):
        self.credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        self.spreadsheet_id = os.getenv("GOOGLE_SHEET_ID")
        
        if not self.credentials_path or not self.spreadsheet_id:
            raise ValueError("Las variables de entorno GOOGLE_APPLICATION_CREDENTIALS y GOOGLE_SHEET_ID deben estar configuradas.")
            
        self.credentials = Credentials.from_service_account_file(
            self.credentials_path, scopes=self.SCOPES
        )
        self.service = build('sheets', 'v4', credentials=self.credentials)
        self.sheet = self.service.spreadsheets()

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
