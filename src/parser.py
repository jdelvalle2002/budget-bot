import os
import json
import logging
import re
from decimal import Decimal
from datetime import date
from pydantic import BaseModel
from google import genai
from google.genai import types

from src.models import Transaction, TipoTransaccion, MetodoPago

logger = logging.getLogger(__name__)

# Cargar configuración de categorías
CATEGORY_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'categories.json')
CATEGORIAS_DISPONIBLES = []
CATEGORIAS_DICT = {}
if os.path.exists(CATEGORY_FILE):
    with open(CATEGORY_FILE, 'r', encoding='utf-8') as f:
        CATEGORIAS_DICT = json.load(f)
        CATEGORIAS_DISPONIBLES = list(CATEGORIAS_DICT.keys())
else:
    CATEGORIAS_DISPONIBLES = ["Alimentación", "Deportes", "Hogar", "Inversiones", "Mesada", "Salidas", "Salud", "Telefonía", "Transporte", "Remuneraciones", "Otros Gastos", "Otros Ingresos"]

class ParseResult(BaseModel):
    transaction: Transaction
    es_ambiguo: bool
    opciones_categoria: list[str]

def try_fast_path(text: str, message_id: str) -> ParseResult | None:
    """
    Intenta parsear mensajes simples con Regex para evitar llamar al LLM.
    Ejemplo válido: "15000 uber", "2500 lider"
    """
    match = re.match(r'^\s*(?P<monto>\d+)\s+(?P<concepto>.+)\s*$', text.lower())
    if match:
        monto_str = match.group('monto')
        concepto_str = match.group('concepto').strip()
        
        # Buscar el concepto en nuestro diccionario de categorías
        for categoria, palabras in CATEGORIAS_DICT.items():
            if concepto_str in palabras:
                tx = Transaction(
                    id_transaccion=message_id,
                    fecha=date.today(),
                    tipo=TipoTransaccion.EGRESO,
                    monto=Decimal(monto_str),
                    concepto=concepto_str.capitalize(),
                    categoria=categoria,
                    metodo=MetodoPago.DEBITO,
                    comentarios="Procesado por Fast-Path (Regex)"
                )
                return ParseResult(transaction=tx, es_ambiguo=False, opciones_categoria=[])
    return None

def get_system_prompt() -> str:
    hoy = date.today().isoformat()
    return f"""
Eres un asistente financiero experto. Tu tarea es extraer información estructurada a partir de mensajes de texto coloquiales.
Debes devolver un JSON estricto que cumpla con el esquema requerido.

HOY ES: {hoy}. Usa esta fecha de referencia estricta para cálculos de tiempo como "ayer", "el martes pasado", "hace 3 días".

Reglas de negocio:
1. 'monto': Debe ser un número entero o decimal positivo. Si el usuario menciona "lucas" o "k" (en Chile), asume miles. Si dice "gambas", son cientos. Si es un reembolso, el monto sigue siendo positivo pero el tipo cambia.
2. 'tipo': Debe ser estrictamente "Ingreso" o "Egreso". (Si fue un gasto, es Egreso. Si dice "me pagaron", "sueldo", "reembolso", "devolución", es Ingreso).
3. 'concepto': Breve resumen de la transacción en 1 o 2 palabras (ej. "Uber", "Cerveza", "Sueldo", "Deuda Pedro").
4. 'categoria': DEBE ser EXACTAMENTE una de las siguientes opciones textuales: {CATEGORIAS_DISPONIBLES}.
5. 'metodo': Debe ser "Débito", "Crédito", "Efectivo", "Transferencia", o "Otro". Si no menciona, asume "Débito".
6. 'fecha': Deduce la fecha exacta en formato YYYY-MM-DD. Si no hay referencia, asume {hoy}.
7. 'comentarios': Opcional, guarda notas extra.
8. 'es_ambiguo': Si el gasto puede encajar razonablemente en dos o más categorías distintas (por ejemplo, "4 lucas de helado" puede ser 'Alimentación' o 'Salidas'), debes marcar esto como true.
9. 'opciones_categoria': Si marcaste 'es_ambiguo' como true, debes enviar un arreglo con las 2 opciones de categoría más probables sacadas estrictamente de {CATEGORIAS_DISPONIBLES}. Si es_ambiguo es false, devuelve un arreglo vacío.
"""

class TransactionExtraction(BaseModel):
    """Esquema de respuesta esperado de Gemini."""
    tipo: TipoTransaccion
    monto: Decimal
    concepto: str
    categoria: str
    metodo: MetodoPago
    fecha: date | None = None
    comentarios: str | None = ""
    es_ambiguo: bool = False
    opciones_categoria: list[str] = []

def parse_transaction_message(text: str, message_id: str) -> ParseResult:
    """
    Parsea un mensaje de texto usando Regex o Gemini AI y devuelve un ParseResult.
    """
    # 1. Intentar Vía Rápida (Ahorra LLM)
    fast_result = try_fast_path(text, message_id)
    if fast_result:
        logger.info(f"FAST-PATH ACTIVADO para el mensaje: '{text}'")
        return fast_result

    # 2. Fallback a Inteligencia Artificial
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("La variable GEMINI_API_KEY no está configurada en tu .env o Render.")
        
    client = genai.Client(api_key=api_key)
    
    try:
        # Nota: Mantengo el modelo que elegiste probar manualmente
        chat = client.chats.create(
            model='gemini-flash-lite-latest',
            config=types.GenerateContentConfig(
                system_instruction=get_system_prompt(),
                response_mime_type="application/json",
                response_schema=TransactionExtraction,
                temperature=0.0
            )
        )
        
        response = chat.send_message(f"Extrae los datos de esta transacción: '{text}'")
        extracted_data = json.loads(response.text)
        
        es_ambiguo = extracted_data.pop("es_ambiguo", False)
        opciones_categoria = extracted_data.pop("opciones_categoria", [])
        
        fecha_str = extracted_data.pop("fecha", None)
        fecha_tx = date.today()
        if fecha_str:
            if isinstance(fecha_str, str):
                fecha_tx = date.fromisoformat(fecha_str)
            else:
                fecha_tx = fecha_str
        
        tx = Transaction(
            id_transaccion=message_id,
            fecha=fecha_tx,
            **extracted_data
        )
        
        return ParseResult(transaction=tx, es_ambiguo=es_ambiguo, opciones_categoria=opciones_categoria)
        
    except Exception as e:
        logger.error(f"Error procesando mensaje con Gemini: {e}")
        raise ValueError(f"Fallo en la IA al intentar parsear el mensaje. ¿Es muy confuso? Error interno: {e}")
