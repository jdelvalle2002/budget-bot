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

def format_currency(monto: Decimal) -> str:
    symbol = os.getenv("CURRENCY_SYMBOL", "$")
    decimals = int(os.getenv("CURRENCY_DECIMALS", "0"))
    return f"{symbol}{monto:,.{decimals}f}"

class TipoTransaccion(str, Enum):
    INGRESO = "Ingreso"
    GASTO = "Gasto"

class MetodoPago(str, Enum):
    DEBITO = "Débito"
    CREDITO = "Crédito"
    EFECTIVO = "Efectivo"
    TRANSFERENCIA = "Transferencia"
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
        if diferencia > 45:
            raise ValueError(f'El gasto es demasiado antiguo ({diferencia} días). El límite es de 45 días.')
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
