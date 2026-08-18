import os
import json
import logging
import re
from decimal import Decimal
from datetime import date
from pydantic import BaseModel
from google import genai
from google.genai import types

from src.models import Transaction, TipoTransaccion, MetodoPago, get_hoy_santiago

logger = logging.getLogger(__name__)

# Archivo de categorías ahora es dinámico desde Google Sheets
CATEGORIAS_DICT = {}
if os.path.exists(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'categories.json')):
    with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'categories.json'), 'r', encoding='utf-8') as f:
        CATEGORIAS_DICT = json.load(f)

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
                    fecha=get_hoy_santiago(),
                    tipo=TipoTransaccion.GASTO,
                    monto=Decimal(monto_str),
                    concepto=concepto_str.capitalize(),
                    categoria=categoria,
                    metodo=MetodoPago.DEBITO,
                    comentarios="Procesado por Fast-Path (Regex)"
                )
                return ParseResult(transaction=tx, es_ambiguo=False, opciones_categoria=[])
    return None

def get_system_prompt(categorias_disponibles: list[str]) -> str:
    hoy = get_hoy_santiago().isoformat()
    return f"""
Eres un asistente financiero experto. Tu tarea es extraer información estructurada a partir de mensajes de texto coloquiales.
Debes devolver un JSON estricto que cumpla con el esquema requerido.

HOY ES: {hoy}. Usa esta fecha de referencia estricta para cálculos de tiempo como "ayer", "el martes pasado", "hace 3 días".

Reglas de negocio:
1. 'es_transaccion': Evalúa si el texto relata un gasto o ingreso REAL Y PROPIO del usuario. Si es una historia sobre otra persona (ej. "mi amigo gastó...", "él me contó..."), una conversación general, o spam, marca esto como false.
2. 'monto': Debe ser un número entero o decimal positivo. Si el usuario menciona "lucas" o "k" (en Chile), asume miles. Si dice "gambas", son cientos. Si dice "quinas" está aludiendo a la moneda de quinientos (ej. 3 quinas = 1500). Si es un reembolso, el monto sigue siendo positivo pero el tipo cambia.
3. 'tipo': Debe ser estrictamente "Ingreso" o "Gasto". (Si fue un Egreso, es gasto. Si dice "me pagaron", "sueldo", "reembolso", "devolución", es Ingreso).
4. 'concepto': Breve resumen de la transacción en 1 o 2 palabras (ej. "Uber", "Cerveza", "Sueldo", "Deuda Pedro").
5. 'categoria': DEBE ser EXACTAMENTE una de las siguientes opciones textuales: {categorias_disponibles}.
6. 'metodo': Debe ser "Débito", "Crédito", "Efectivo", "Transferencia", o "Otro". Si no menciona, asume "Débito".
7. 'fecha': Deduce la fecha exacta en formato YYYY-MM-DD. Si no hay referencia, asume {hoy}. Debes tener cuidado, la compra puede aludir al futuro pero haber ocurrido en el presente o pasado. Por ejemplo puedo comprar algo hoy para una actividad de la próxima semana.
8. 'comentarios': Opcional, guarda notas extra para entender el movimiento al revisar los datos.
9. 'es_ambiguo': Si el gasto puede encajar razonablemente en dos o más categorías distintas (por ejemplo, "4 lucas de helado" o "10k cervezas" pueden ser 'Alimentación' o 'Salidas', "regalo" puede ser 'Otros Gastos' o 'Mesada', "15k uber al estadio" puede ser 'Transporte' o 'Salidas'), debes marcar esto como true.
10. 'opciones_categoria': Si marcaste 'es_ambiguo' como true, debes enviar un arreglo con las 2 opciones de categoría más probables sacadas estrictamente de {categorias_disponibles}. Si es_ambiguo es false, devuelve un arreglo vacío.
"""

class TransactionExtraction(BaseModel):
    """Esquema de respuesta esperado de Gemini."""
    es_transaccion: bool = True
    tipo: TipoTransaccion
    monto: Decimal
    concepto: str
    categoria: str
    metodo: MetodoPago
    fecha: date | None = None
    comentarios: str | None = ""
    es_ambiguo: bool = False
    opciones_categoria: list[str] = []

class MultiTransactionExtraction(BaseModel):
    """Esquema para extraer múltiples transacciones."""
    transacciones: list[TransactionExtraction]

def parse_transaction_message(text: str, message_id: str, categorias_disponibles: list[str]) -> ParseResult:
    """
    Parsea un mensaje de texto usando Regex o Gemini AI y devuelve un ParseResult.
    """
    # 1. Intentar Vía Rápida (Ahorra LLM)
    fast_result = try_fast_path(text, message_id)
    if fast_result:
        logger.info(f"FAST-PATH ACTIVADO para el mensaje (ID: {message_id})")
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
                system_instruction=get_system_prompt(categorias_disponibles),
                response_mime_type="application/json",
                response_schema=TransactionExtraction,
                temperature=0.01
            )
        )
        
        response = chat.send_message(f"Extrae los datos de esta transacción: '{text}'")
        
        try:
            extracted_data = json.loads(response.text)
        except json.JSONDecodeError:
            raise ValueError("La Inteligencia Artificial se confundió y no devolvió un formato válido. ¿Podrías redactar tu gasto de forma más clara?")
        
        es_transaccion = extracted_data.pop("es_transaccion", True)
        if not es_transaccion:
            raise ValueError("El mensaje no parece ser un gasto o ingreso tuyo. (A lo mejor me estabas contando una historia o saludando 👋)")
        
        es_ambiguo = extracted_data.pop("es_ambiguo", False)
        opciones_categoria = extracted_data.pop("opciones_categoria", [])
        
        fecha_str = extracted_data.pop("fecha", None)
        fecha_tx = get_hoy_santiago()
        if fecha_str:
            if isinstance(fecha_str, str):
                try:
                    fecha_tx = date.fromisoformat(fecha_str)
                except ValueError:
                    pass
            else:
                fecha_tx = fecha_str
        
        tx = Transaction(
            id_transaccion=message_id,
            fecha=fecha_tx,
            **extracted_data
        )
        
        return ParseResult(transaction=tx, es_ambiguo=es_ambiguo, opciones_categoria=opciones_categoria)

    except ValueError as ve:
        # Errores lanzados manualmente (ej. 'es_transaccion' == False o JSONDecodeError convertido)
        raise ve
    except Exception as e:
        # Verificar si es un ValidationError de Pydantic (usamos el nombre de clase por si cambia el import)
        if e.__class__.__name__ == 'ValidationError':
            errores_legibles = []
            for err in e.errors(): # type: ignore
                campo = err.get('loc', ['desconocido'])[0]
                msg = err.get('msg', '')
                
                # Limpiar los textos técnicos de Pydantic
                if err.get('type') == 'missing':
                    msg = "Falta este dato o no lo mencionaste claramente."
                elif msg.startswith("Value error, "):
                    msg = msg.replace("Value error, ", "")
                elif "Input should be a valid" in msg or "Input should be" in msg:
                    msg = "Valor no reconocido o formato incorrecto."
                
                # Traducir los campos para el usuario
                if campo == 'monto':
                    errores_legibles.append(f"💰 Monto: {msg}")
                elif campo == 'fecha':
                    errores_legibles.append(f"📅 Fecha: {msg}")
                elif campo == 'categoria':
                    errores_legibles.append(f"📁 Categoría: {msg}")
                elif campo == 'tipo':
                    errores_legibles.append(f"🔄 Tipo: {msg}")
                else:
                    errores_legibles.append(f"📝 {str(campo).capitalize()}: {msg}")
            
            raise ValueError("Me faltaron datos o hubo un error de formato:\n" + "\n".join(errores_legibles))
        
        logger.error(f"Error procesando mensaje con Gemini: {e}")
        raise ValueError("Fallo en la IA al intentar procesar el mensaje. Inténtalo de nuevo.")

def responder_consulta_natural(pregunta: str, datos_json: str) -> str:
    """
    Toma una pregunta financiera y un historial de transacciones en JSON,
    y utiliza Gemini para generar una respuesta analítica.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("La variable GEMINI_API_KEY no está configurada.")
        
    client = genai.Client(api_key=api_key)
    
    hoy = get_hoy_santiago().isoformat()
    
    prompt_sistema = f"""
Eres el analista financiero personal del usuario. 
HOY ES: {hoy}.
Te daré un JSON con el historial reciente de transacciones de Google Sheets del usuario.
Tu tarea es responder a la pregunta analítica del usuario de forma amigable, clara, breve y matemáticamente precisa usando los datos proveídos.
Puedes calcular sumas, identificar promedios, o encontrar gastos específicos.
Si la información no está en el JSON, díselo de forma honesta. No inventes datos.
Usa emojis para hacer la respuesta más amigable.
Responde directamente, sin usar markdown extra de código JSON o saludos muy formales.
"""
    try:
        chat = client.chats.create(
            model='gemini-flash-lite-latest',
            config=types.GenerateContentConfig(
                system_instruction=prompt_sistema,
                temperature=0.1
            )
        )
        
        contexto = f"DATOS DE TRANSACCIONES (JSON):\n{datos_json}\n\nPREGUNTA DEL USUARIO:\n{pregunta}"
        response = chat.send_message(contexto)
        return str(response.text)
    except Exception as e:
        logger.error(f"Error en consulta natural: {e}")
        raise ValueError("Lo siento, mis circuitos analíticos fallaron al procesar tantos datos. Intenta nuevamente.")

def parse_multi_transaction_message(text: str, base_message_id: str, categorias_disponibles: list[str]) -> list[Transaction]:
    """
    Parsea un mensaje que contiene múltiples gastos usando Gemini.
    Asigna IDs secuenciales basados en el base_message_id.
    Fuerza a Gemini a resolver ambigüedades sin preguntar al usuario.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("La variable GEMINI_API_KEY no está configurada.")
        
    client = genai.Client(api_key=api_key)
    
    prompt_multi = get_system_prompt(categorias_disponibles)
    prompt_multi += "\n\nREGLA ESPECIAL PARA MÚLTIPLES REGISTROS:\n"
    prompt_multi += "El usuario enviará múltiples transacciones en un solo mensaje.\n"
    prompt_multi += "Debes extraer TODAS en el arreglo 'transacciones'.\n"
    prompt_multi += "IMPORTANTE: Prohibido marcar 'es_ambiguo' como true. DEBES elegir la categoría que te parezca más adecuada para cada transacción y forzar es_ambiguo a false."
    
    try:
        chat = client.chats.create(
            model='gemini-flash-lite-latest',
            config=types.GenerateContentConfig(
                system_instruction=prompt_multi,
                response_mime_type="application/json",
                response_schema=MultiTransactionExtraction,
                temperature=0.01
            )
        )
        
        response = chat.send_message(f"Extrae los datos de estas transacciones: '{text}'")
        
        try:
            extracted_data = json.loads(response.text)
        except json.JSONDecodeError:
            raise ValueError("La Inteligencia Artificial se confundió al procesar múltiples registros. Intenta enviarlos más separados.")
            
        lista_tx_dicts = extracted_data.get("transacciones", [])
        if not lista_tx_dicts:
            raise ValueError("No pude encontrar ninguna transacción clara en tu mensaje múltiple.")
            
        transacciones_finales = []
        
        for i, tx_data in enumerate(lista_tx_dicts):
            # Limpieza básica
            _ = tx_data.pop("es_transaccion", True) # Ignoramos validaciones individuales
            _ = tx_data.pop("es_ambiguo", False)
            _ = tx_data.pop("opciones_categoria", [])
            
            fecha_str = tx_data.pop("fecha", None)
            fecha_tx = get_hoy_santiago()
            if fecha_str:
                if isinstance(fecha_str, str):
                    try:
                        fecha_tx = date.fromisoformat(fecha_str)
                    except ValueError:
                        pass
                else:
                    fecha_tx = fecha_str
            
            tx = Transaction(
                id_transaccion=f"{base_message_id}-{i+1}",
                fecha=fecha_tx,
                **tx_data
            )
            transacciones_finales.append(tx)
            
        return transacciones_finales
        
    except ValueError as ve:
        raise ve
    except Exception as e:
        logger.error(f"Error procesando mensaje múltiple con Gemini: {e}")
        raise ValueError("Hubo un error interno o de formato procesando las múltiples transacciones.")
