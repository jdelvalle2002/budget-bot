from pydantic import BaseModel, Field, field_validator
from decimal import Decimal
from typing import Optional
from datetime import date
from enum import Enum
import uuid

class TipoTransaccion(str, Enum):
    INGRESO = "Ingreso"
    EGRESO = "Egreso"

class MetodoPago(str, Enum):
    DEBITO = "Débito"
    CREDITO = "Crédito"
    EFECTIVO = "Efectivo"
    TRANSFERENCIA = "Transferencia"
    OTRO = "Otro"

class Transaction(BaseModel):
    id_transaccion: str = Field(default_factory=lambda: str(uuid.uuid4()))
    fecha: date = Field(default_factory=date.today)
    tipo: TipoTransaccion
    monto: Decimal
    concepto: str
    categoria: str
    metodo: MetodoPago = MetodoPago.DEBITO
    comentarios: Optional[str] = ""

    @field_validator('monto')
    @classmethod
    def monto_debe_ser_positivo(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError('El monto debe ser estrictamente positivo. Usa el campo "tipo" para definir si es ingreso o egreso.')
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
