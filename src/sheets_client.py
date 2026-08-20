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

    def append_transaction(self, transaction: Transaction, sheet_name: str = None) -> bool:
        """
        Inserta una transacción en Google Sheets si su ID no existe previamente.
        Retorna True si la inserción fue exitosa o si ya existía (idempotencia cumplida),
        False si hubo un error.
        """
        if not sheet_name:
            sheet_name = str(transaction.fecha.year)
            
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

    def update_transaction(self, transaction: Transaction, sheet_name: str = None) -> bool:
        """Sobrescribe una transacción existente."""
        if not sheet_name:
            sheet_name = str(transaction.fecha.year)
            
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

    def append_multiple_transactions(self, transactions: list, sheet_name: str = None) -> bool:
        """Añade múltiples transacciones de una sola vez."""
        if not transactions:
            return True
            
        if not sheet_name:
            sheet_name = str(transactions[0].fecha.year)
        range_name = f"{sheet_name}!A:H"
        try:
            values = [tx.to_row() for tx in transactions]
            body = {'values': values}
            result = self.sheet.values().append(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body=body
            ).execute()
            logger.info(f"Múltiples filas añadidas.")
            return True
        except Exception as e:
            logger.error(f"Error al añadir múltiples transacciones: {e}")
            return False
    def get_last_transactions(self, limit: int = 5, sheet_name: str = None) -> list[dict]:
        """Devuelve las últimas N transacciones registradas (útil para el bot de Telegram)."""
        if not sheet_name:
            from src.models import get_local_date
            sheet_name = str(get_local_date().year)
            
        range_name = f"{sheet_name}!A:H"
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()
            values = result.get('values', [])
            
            if not values or len(values) <= 1:
                return []
                
            # Asumimos que la fila 0 es el encabezado
            filas_datos = values[1:]
            
            # Limpiar filas vacías
            filas_datos = [row for row in filas_datos if len(row) > 0 and str(row[0]).strip() != ""]
            
            ultimas = filas_datos[-limit:]
            transacciones = []
            
            for row in ultimas:
                if len(row) >= 6: # Asegurar que tiene hasta la categoría mínimo
                    try:
                        monto = float(str(row[3]).replace(',', ''))
                    except ValueError:
                        monto = 0
                        
                    tx = {
                        "id": row[0],
                        "fecha": row[1],
                        "tipo": row[2],
                        "monto": monto,
                        "concepto": row[4],
                        "categoria": row[5],
                        "metodo": row[6] if len(row) > 6 else "Desconocido",
                        "comentarios": row[7] if len(row) > 7 else ""
                    }
                    transacciones.append(tx)
            
            return transacciones
        except Exception as e:
            logger.error(f"Error obteniendo últimas transacciones: {e}")
            return []

    def search_transactions(self, query: str, limit: int = 5, sheet_name: str = None) -> list[dict]:
        """Busca transacciones que coincidan con el query (concepto, categoría, comentarios)."""
        if not sheet_name:
            from src.models import get_local_date
            sheet_name = str(get_local_date().year)
            
        range_name = f"{sheet_name}!A:H"
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()
            values = result.get('values', [])
            
            if not values or len(values) <= 1:
                return []
                
            filas_datos = values[1:]
            filas_datos = [row for row in filas_datos if len(row) > 0 and str(row[0]).strip() != ""]
            
            query_lower = str(query).lower()
            matched_transactions = []
            
            # Recorrer de más reciente a más antiguo
            for row in reversed(filas_datos):
                row_str = " ".join([str(cell).lower() for cell in row])
                if query_lower in row_str:
                    if len(row) >= 6:
                        try:
                            monto = float(str(row[3]).replace(',', ''))
                        except ValueError:
                            monto = 0
                            
                        tx = {
                            "id": row[0],
                            "fecha": row[1],
                            "tipo": row[2],
                            "monto": monto,
                            "concepto": row[4],
                            "categoria": row[5],
                            "metodo": row[6] if len(row) > 6 else "Desconocido",
                            "comentarios": row[7] if len(row) > 7 else ""
                        }
                        matched_transactions.append(tx)
                        
                        if len(matched_transactions) == limit:
                            break
                            
            return matched_transactions
        except Exception as e:
            logger.error(f"Error buscando transacciones: {e}")
            return []

    def get_month_summary(self, month_offset: int = 0, sheet_name: str = None, max_day: int = None) -> tuple[dict, int, int]:
        """
        Obtiene el resumen de gastos/ingresos para un mes.
        Si max_day está definido, solo suma hasta ese día inclusive.
        """
        from src.models import get_local_date
        hoy = get_local_date()
        
        if not sheet_name:
            sheet_name = str(hoy.year)
        
        target_month = hoy.month + month_offset
        target_year = hoy.year
        
        while target_month <= 0:
            target_month += 12
            target_year -= 1
            
        while target_month > 12:
            target_month -= 12
            target_year += 1
            
        mes_objetivo = f"{target_year}-{target_month:02d}"
        
        range_name = f"{sheet_name}!A:H"
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()
            values = result.get('values', [])
            
            resumen = {}
            # Categorías de ingresos nativas (donde el ingreso suma positivamente)
            categorias_ingreso = ["Remuneraciones", "Otros Ingresos", "Inversiones"]
            
            for row in values[1:]: # Saltar encabezado
                if len(row) >= 6:
                    fecha_str = row[1]
                    tipo = row[2]
                    monto_str = str(row[3]).replace(',', '')
                    categoria = row[5]
                    
                    if fecha_str.startswith(mes_objetivo):
                        if max_day is not None:
                            try:
                                day = int(fecha_str.split('T')[0].split('-')[2])
                                if day > max_day:
                                    continue
                            except Exception:
                                pass
                                
                        try:
                            monto = float(monto_str)
                            if categoria not in resumen:
                                resumen[categoria] = {"total": 0, "count": 0}
                            
                            is_ingreso_nativo = categoria in categorias_ingreso
                            
                            if tipo.lower() == "gasto":
                                if is_ingreso_nativo:
                                    resumen[categoria]["total"] -= monto # Gasto en categoría de ingreso resta
                                else:
                                    resumen[categoria]["total"] += monto # Gasto en categoría de gasto suma
                                resumen[categoria]["count"] += 1
                                
                            elif tipo.lower() == "ingreso":
                                if is_ingreso_nativo:
                                    resumen[categoria]["total"] += monto # Ingreso en categoría de ingreso suma
                                else:
                                    resumen[categoria]["total"] -= monto # Ingreso en categoría de gasto resta (NETEO)
                                # No sumamos al 'count' de transacciones de gasto principal, o sí? Lo dejamos sumar para saber que hubo movimientos.
                                resumen[categoria]["count"] += 1
                        except ValueError:
                            continue
            return resumen, target_month, target_year
        except Exception as e:
            logger.error(f"Error generando resumen: {e}")
            return {}, target_month, target_year
    def get_category_monthly_average(self, categoria: str, sheet_name: str = None) -> float:
        """Calcula el gasto/ingreso neto mensual promedio histórico para una categoría específica."""
        from src.models import get_local_date
        if not sheet_name:
            sheet_name = str(get_local_date().year)
            
        range_name = f"{sheet_name}!A:H"
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()
            values = result.get('values', [])
            
            meses_totales = {}
            categorias_ingreso = ["Remuneraciones", "Otros Ingresos", "Inversiones"]
            is_ingreso_nativo = categoria in categorias_ingreso
            
            for row in values[1:]:
                if len(row) >= 6:
                    fecha_str = row[1]
                    tipo = row[2]
                    monto_str = str(row[3]).replace(',', '')
                    cat_row = row[5]
                    
                    if cat_row.lower() == categoria.lower():
                        mes = fecha_str[:7] # YYYY-MM
                        if mes not in meses_totales:
                            meses_totales[mes] = 0.0
                            
                        try:
                            monto = float(monto_str)
                            if tipo.lower() == "gasto":
                                meses_totales[mes] += (-monto if is_ingreso_nativo else monto)
                            elif tipo.lower() == "ingreso":
                                meses_totales[mes] += (monto if is_ingreso_nativo else -monto)
                        except ValueError:
                            continue
            
            if not meses_totales:
                return 0.0
                
            total = sum(meses_totales.values())
            return total / len(meses_totales)
        except Exception as e:
            logger.error(f"Error calculando promedio histórico de {categoria}: {e}")
            return 0.0
            
    def delete_transaction(self, id_transaccion: str, sheet_name: str = None) -> bool:
        """Borra la fila de una transacción de forma definitiva."""
        if not sheet_name:
            from src.models import get_local_date
            sheet_name = str(get_local_date().year)
            
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

    def load_categories_from_config(self, force_refresh: bool = False) -> dict:
        """
        Lee la pestaña 'Config' (A:C) para obtener las categorías, colores y presupuesto.
        Retorna un dict: {"Alimentación": {"color": "#E74C3C", "presupuesto": 200000}, ...}
        """
        if hasattr(self, '_cached_categories') and self._cached_categories and not force_refresh:
            return self._cached_categories
            
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range="Config!A2:C"
            ).execute()
            values = result.get('values', [])
            
            categorias = {}
            for row in values:
                if len(row) > 0 and row[0].strip():
                    cat_name = row[0].strip()
                    cat_color = row[1].strip() if len(row) > 1 and row[1].strip() else None
                    
                    presupuesto = None
                    if len(row) > 2 and row[2].strip():
                        try:
                            # Limpiar símbolos de moneda
                            presup_str = str(row[2]).replace('$', '').replace('€', '').strip()
                            
                            # Heurística para separadores de miles vs decimales
                            if presup_str.count('.') > 0:
                                # Si el último punto tiene exactamente 3 dígitos a su derecha, asumimos separador de miles (ej. 200.000)
                                if len(presup_str.split('.')[-1]) == 3:
                                    presup_str = presup_str.replace('.', '')
                                    
                            if presup_str.count(',') > 0:
                                # Si la última coma tiene 3 dígitos a su derecha, asumimos separador de miles (ej. 200,000)
                                if len(presup_str.split(',')[-1]) == 3:
                                    presup_str = presup_str.replace(',', '')
                                else:
                                    # Si no, asumimos que es separador decimal (ej. 15,50)
                                    presup_str = presup_str.replace(',', '.')
                                    
                            presupuesto = float(presup_str)
                        except ValueError:
                            presupuesto = None
                            
                    categorias[cat_name] = {"color": cat_color, "presupuesto": presupuesto}
                    
            self._cached_categories = categorias
            return categorias
        except Exception as e:
            logger.error(f"Error leyendo Config: {e}. Asegúrate de haber ejecutado setup_sheet.py")
            # Fallback hardcodeado si falla la API o la pestaña no existe
            return {
                "Alimentación": {"color": "#E74C3C", "presupuesto": None},
                "Deportes": {"color": "#3498DB", "presupuesto": None},
                "Hogar": {"color": "#2ECC71", "presupuesto": None},
                "Inversiones": {"color": "#F39C12", "presupuesto": None},
                "Mesada": {"color": "#9B59B6", "presupuesto": None},
                "Salidas": {"color": "#E84393", "presupuesto": None},
                "Salud": {"color": "#16A085", "presupuesto": None},
                "Telefonía": {"color": "#1ABC9C", "presupuesto": None},
                "Transporte": {"color": "#F1C40F", "presupuesto": None},
                "Remuneraciones": {"color": "#27AE60", "presupuesto": None},
                "Otros Gastos": {"color": "#7F8C8D", "presupuesto": None},
                "Otros Ingresos": {"color": "#D35400", "presupuesto": None}
            }
