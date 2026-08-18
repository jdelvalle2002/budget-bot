import os
import json
import logging
import re
from decimal import Decimal
from datetime import date
from enum import Enum
from collections import defaultdict
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

class FiltroTiempo(str, Enum):
    ESTE_MES = "este_mes"
    MES_PASADO = "mes_pasado"
    ESTE_AÑO = "este_año"
    SIEMPRE = "siempre"

class IntentType(str, Enum):
    GASTO_TOTAL = "gasto_total"
    GASTO_PROMEDIO = "gasto_promedio"
    DESGLOSE_CATEGORIA = "desglose_categoria"
    DESGLOSE_METODO = "desglose_metodo"
    BUSQUEDA_ESPECIFICA = "busqueda_especifica"

class AnalisisQuery(BaseModel):
    intent: IntentType
    filtro_tiempo: FiltroTiempo
    categoria_objetivo: str | None = None
    concepto_objetivo: str | None = None

def filtrar_transacciones(transacciones: list[dict], filtro_tiempo: FiltroTiempo) -> list[dict]:
    hoy = get_hoy_santiago()
    filtradas = []
    
    for tx in transacciones:
        # Extraer fecha
        fecha_str = tx.get('fecha', '')
        if not fecha_str:
            continue
            
        try:
            # Soportar formato iso o dia/mes/año según como esté en sheets
            # Asumiremos ISO YYYY-MM-DD por defecto de nuestro propio parser
            if "T" in fecha_str:
                fecha_tx = date.fromisoformat(fecha_str.split("T")[0])
            else:
                fecha_tx = date.fromisoformat(fecha_str)
        except ValueError:
            continue
            
        if filtro_tiempo == FiltroTiempo.ESTE_MES:
            if fecha_tx.year == hoy.year and fecha_tx.month == hoy.month:
                filtradas.append(tx)
        elif filtro_tiempo == FiltroTiempo.MES_PASADO:
            mes_pasado = hoy.month - 1 if hoy.month > 1 else 12
            año_pasado = hoy.year if hoy.month > 1 else hoy.year - 1
            if fecha_tx.year == año_pasado and fecha_tx.month == mes_pasado:
                filtradas.append(tx)
        elif filtro_tiempo == FiltroTiempo.ESTE_AÑO:
            if fecha_tx.year == hoy.year:
                filtradas.append(tx)
        else:
            filtradas.append(tx)
            
    return filtradas

def responder_consulta_natural(pregunta: str, transacciones: list[dict]) -> str:
    """
    Motor analítico determinista. Usa Gemini para clasificar la intención,
    y Python para calcular el resultado riguroso matemáticamente.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("La variable GEMINI_API_KEY no está configurada.")
        
    client = genai.Client(api_key=api_key)
    hoy_date = get_hoy_santiago()
    hoy = hoy_date.isoformat()
    
    prompt_router = f"""
    Hoy es {hoy}. 
    Clasifica la intención analítica del usuario sobre sus finanzas.
    """
    
    try:
        chat = client.chats.create(
            model='gemini-flash-lite-latest',
            config=types.GenerateContentConfig(
                system_instruction=prompt_router,
                response_schema=AnalisisQuery,
                response_mime_type="application/json",
                temperature=0.0
            )
        )
        response = chat.send_message(pregunta)
        query_data = json.loads(response.text)
        intent = query_data.get("intent")
        filtro_tiempo = FiltroTiempo(query_data.get("filtro_tiempo", "este_mes"))
        categoria_obj = query_data.get("categoria_objetivo")
        
        txs_filtradas = filtrar_transacciones(transacciones, filtro_tiempo)
        
        # Filtramos solo gastos por defecto, salvo que sea búsqueda
        gastos = [tx for tx in txs_filtradas if str(tx.get('tipo', '')).lower() == 'gasto']
        
        # Filtramos por categoría si viene explícita (para Totales y Promedios)
        if categoria_obj and intent in [IntentType.GASTO_TOTAL, IntentType.GASTO_PROMEDIO]:
            gastos = [tx for tx in gastos if categoria_obj.lower() in tx.get('categoria', '').lower()]

        respuesta_final = ""
        
        if intent == IntentType.GASTO_TOTAL:
            total = sum(Decimal(str(tx.get('monto', 0)).replace(',','').replace('$','')) for tx in gastos)
            cat_str = f" en {categoria_obj}" if categoria_obj else ""
            respuesta_final = f"📊 Tu gasto total{cat_str} ({filtro_tiempo.value.replace('_', ' ')}) es de **${total:,.0f}**."
            
        elif intent == IntentType.GASTO_PROMEDIO:
            total = sum(Decimal(str(tx.get('monto', 0)).replace(',','').replace('$','')) for tx in gastos)
            import calendar
            dias = 1
            if filtro_tiempo == FiltroTiempo.ESTE_MES:
                dias = hoy_date.day
            elif filtro_tiempo == FiltroTiempo.MES_PASADO:
                mes_pasado = hoy_date.month - 1 if hoy_date.month > 1 else 12
                año_pasado = hoy_date.year if hoy_date.month > 1 else hoy_date.year - 1
                dias = calendar.monthrange(año_pasado, mes_pasado)[1]
            elif filtro_tiempo == FiltroTiempo.ESTE_AÑO:
                dias = (hoy_date - date(hoy_date.year, 1, 1)).days + 1
            elif filtro_tiempo == FiltroTiempo.SIEMPRE:
                if gastos:
                    fechas_validas = []
                    for tx in gastos:
                        try:
                            f = tx.get('fecha', '').split("T")[0]
                            fechas_validas.append(date.fromisoformat(f))
                        except: pass
                    if fechas_validas:
                        primera = min(fechas_validas)
                        dias = (hoy_date - primera).days + 1
                dias = max(1, dias)
                
            promedio = total / Decimal(dias)
            cat_str = f" en {categoria_obj}" if categoria_obj else ""
            respuesta_final = f"📉 **Promedio Diario{cat_str} ({filtro_tiempo.value.replace('_', ' ')}):**\nHas gastado una media de **${promedio:,.0f} al día**."
            
        elif intent == IntentType.DESGLOSE_METODO:
            desglose = defaultdict(Decimal)
            for tx in gastos:
                metodo = tx.get('metodo', 'Desconocido')
                desglose[metodo] += Decimal(str(tx.get('monto', 0)).replace(',','').replace('$',''))
            
            respuesta_final = f"💳 **Desglose por Método de Pago ({filtro_tiempo.value.replace('_', ' ')}):**\n"
            total = Decimal(0)
            for m, monto in sorted(desglose.items(), key=lambda x: x[1], reverse=True):
                respuesta_final += f"• {m}: ${monto:,.0f}\n"
                total += monto
            respuesta_final += f"\n**Total:** ${total:,.0f}"
            
        elif intent == IntentType.DESGLOSE_CATEGORIA:
            desglose = defaultdict(Decimal)
            for tx in gastos:
                cat = tx.get('categoria', 'Sin Categoría')
                if categoria_obj and categoria_obj.lower() not in cat.lower():
                    continue
                desglose[cat] += Decimal(str(tx.get('monto', 0)).replace(',','').replace('$',''))
            
            respuesta_final = f"📁 **Desglose por Categoría ({filtro_tiempo.value.replace('_', ' ')}):**\n"
            total = Decimal(0)
            for c, monto in sorted(desglose.items(), key=lambda x: x[1], reverse=True):
                respuesta_final += f"• {c}: ${monto:,.0f}\n"
                total += monto
            respuesta_final += f"\n**Total analizado:** ${total:,.0f}"
            
            # OPINIÓN DE LA IA:
            if total > 0:
                prompt_opinion = f"Acabo de calcular este gasto: {respuesta_final}. Dame 1 frase amable, amistosa y súper breve opinando sobre esta distribución de gastos. No des formato markdown."
                chat_op = client.chats.create(model='gemini-flash-lite-latest')
                opinion = chat_op.send_message(prompt_opinion)
                respuesta_final += f"\n\n🤖 _Comentario: {opinion.text.strip()}_"
                
        else: # Busqueda Especifica o Fallback
            respuesta_final = "🔎 Entendí tu consulta, pero por ahora soy mejor haciendo desgloses matemáticos (Categoría, Método o Total). ¡Prueba preguntarme por sumas específicas!"

        return respuesta_final
        
    except Exception as e:
        logger.error(f"Error en consulta natural determinista: {e}")
        raise ValueError("Lo siento, mis circuitos analíticos fallaron al clasificar tu intención. Intenta nuevamente.")

def generar_comentario_ironico(monto: Decimal, concepto: str, categoria: str) -> str:
    """
    Genera un comentario breve e irónico sobre una transacción recién registrada.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return ""
        
    client = genai.Client(api_key=api_key)
    
    prompt = (
        f"Acabo de registrar este gasto: ${monto:,.0f} "
        f"en '{concepto}' (Categoría: {categoria}). "
        f"Genera un comentario de 1 sola frase muy breve, irónico y un poco sarcástico sobre este gasto para responderle al usuario. "
        f"Hazlo amigable pero con ese toque de humor financiero. No uses formato markdown complejo."
    )
    
    try:
        chat = client.chats.create(
            model='gemini-flash-lite-latest',
            config=types.GenerateContentConfig(temperature=0.85)
        )
        response = chat.send_message(prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Error generando comentario ironico: {e}")
        return ""

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
