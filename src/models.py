from pydantic import BaseModel, Field, field_validator
from decimal import Decimal
from typing import Optional
from datetime import date, datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo
from enum import Enum
import uuid
import os

def get_local_date() -> date:
    tz_name = os.getenv("TZ_NAME", "America/Santiago")
    return datetime.now(ZoneInfo(tz_name)).date()

def parse_flexible_date(fecha_val: str | date | datetime | None) -> date | None:
    """
    Convierte de forma robusta cualquier representación de fecha a datetime.date.
    Soporta:
    - Instancias de date o datetime
    - Formatos ISO: YYYY-MM-DD, YYYY/MM/DD, YYYY-MM-DDTHH:MM:SS
    - Formatos latinos: DD-MM-YYYY, DD/MM/YYYY, D-M-YYYY, D/M/YYYY
    - Formatos cortos: DD-MM-YY, DD/MM/YY
    Retorna None si la fecha no es válida o está vacía.
    """
    if fecha_val is None:
        return None
    if isinstance(fecha_val, datetime):
        return fecha_val.date()
    if isinstance(fecha_val, date):
        return fecha_val

    s = str(fecha_val).strip()
    if not s:
        return None

    s_date = s.split("T")[0].split(" ")[0].strip().replace("/", "-")
    parts = s_date.split("-")
    if len(parts) == 3:
        try:
            p0, p1, p2 = parts[0], parts[1], parts[2]
            if len(p0) == 4:  # YYYY-MM-DD
                return date(int(p0), int(p1), int(p2))
            elif len(p2) == 4:  # DD-MM-YYYY
                return date(int(p2), int(p1), int(p0))
            elif len(p2) == 2:  # DD-MM-YY (asume siglo 21)
                return date(2000 + int(p2), int(p1), int(p0))
        except (ValueError, IndexError):
            pass

    try:
        return date.fromisoformat(s.split("T")[0])
    except (ValueError, TypeError):
        pass

    return None

def format_currency(monto: Decimal) -> str:
    symbol = os.getenv("CURRENCY_SYMBOL", "$")
    decimals = int(os.getenv("CURRENCY_DECIMALS", "0"))
    if monto < 0:
        return f"-{symbol}{abs(monto):,.{decimals}f}"
    return f"{symbol}{monto:,.{decimals}f}"

class TipoTransaccion(str, Enum):
    INGRESO = "Ingreso"
    GASTO = "Gasto"

class MetodoPago(str, Enum):
    DEBITO = "Débito"
    CREDITO = "Crédito"
    EFECTIVO = "Efectivo"
    TRANSFERENCIA = "Transferencia"
    PLANILLA = "Planilla"
    OTRO = "Otro"

class Transaction(BaseModel):
    id_transaccion: str = Field(default_factory=lambda: str(uuid.uuid4()))
    fecha: date = Field(default_factory=get_local_date)
    tipo: TipoTransaccion
    monto: Decimal
    concepto: str
    categoria: str
    metodo: MetodoPago = MetodoPago.DEBITO
    comentarios: Optional[str] = ""

    @field_validator('fecha')
    @classmethod
    def fecha_dentro_de_rango(cls, v: date) -> date:
        hoy = get_local_date()
        if v > hoy:
            raise ValueError('No puedes registrar gastos o ingresos en el futuro.')
        diferencia = (hoy - v).days
        
        try:
            max_past_days = int(os.getenv("MAX_PAST_DAYS", "60"))
        except ValueError:
            max_past_days = 60
            
        if max_past_days > 0 and diferencia > max_past_days:
            raise ValueError(f'El gasto es demasiado antiguo ({diferencia} días). El límite es de {max_past_days} días.')
        return v

    @field_validator('monto')
    @classmethod
    def monto_debe_ser_positivo(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError('El monto debe ser estrictamente positivo. Usa el campo "tipo" para definir si es ingreso o gasto.')
        return v
    
    def to_row(self) -> list:
        """Convierte el modelo en una fila (lista) para insertar en Google Sheets."""
        return [
            self.id_transaccion,
            self.fecha.isoformat(),
            self.tipo.value,
            str(self.monto),  # Evitar problemas de coma flotante en Google Sheets enviándolo como texto limpio
            self.concepto,
            self.categoria,
            self.metodo.value,
            self.comentarios or ""
        ]
