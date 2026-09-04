import os
import json
import logging
import re
import random
from decimal import Decimal
from datetime import date, datetime, timedelta
from enum import Enum
from collections import defaultdict
import unicodedata
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from src.models import Transaction, TipoTransaccion, MetodoPago, get_local_date, format_currency

logger = logging.getLogger(__name__)

def quitar_acentos(s: str) -> str:
    if not s:
        return ""
    return ''.join(c for c in unicodedata.normalize('NFD', str(s)) if unicodedata.category(c) != 'Mn')

# Archivo de categorías ahora es dinámico desde Google Sheets
CATEGORIAS_DICT = {}
if os.path.exists(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'categories.json')):
    with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'categories.json'), 'r', encoding='utf-8') as f:
        CATEGORIAS_DICT = json.load(f)

class ParseResult(BaseModel):
    transaction: Transaction
    es_ambiguo: bool
    opciones_categoria: list[str]
    es_ambiguo_metodo: bool = False
    opciones_metodo: list[str] = []

def try_fast_path(text: str, message_id: str) -> ParseResult | None:
    """
    Intenta parsear mensajes simples con Regex para evitar llamar al LLM.
    Ejemplos válidos:
    - "15000 uber", "2500 lider"
    - "3500 almuerzo menu casino pega", "almuerzo casino pega 4000", "3500 casino pega"
    - "3500 por planilla", "almuerzo por planilla 3500"
    """
    text_clean = text.lower().strip()
    
    # 1. Fast-path para almuerzos / casino de trabajo descontados por planilla
    patrones_casino_almuerzo = [
        r'casino\s+(?:de\s+la\s+)?pega',
        r'casino\s+(?:del?\s+)?trabajo',
        r'(?:almuerzo|menu|menú|colaci[oó]n)\s+(?:de\s+la\s+)?pega',
        r'(?:almuerzo|menu|menú|colaci[oó]n)\s+(?:por|en)\s+planilla',
        r'casino\s+(?:por|en)\s+planilla',
        r'(?:marqu[eé]\s+)?credencial\s+casino',
    ]
    es_casino_almuerzo = any(re.search(p, text_clean) for p in patrones_casino_almuerzo)
    tiene_otro_metodo = any(m in text_clean for m in ["debito", "débito", "credito", "crédito", "efectivo", "transferencia"])
    
    if es_casino_almuerzo and not tiene_otro_metodo:
        monto_match = re.search(r'\b(?P<monto>\d{3,8})\b', text_clean)
        if monto_match:
            monto_str = monto_match.group('monto')
            tx = Transaction(
                id_transaccion=message_id,
                fecha=get_local_date(),
                tipo=TipoTransaccion.GASTO,
                monto=Decimal(monto_str),
                concepto="Almuerzo Casino",
                categoria="Alimentación",
                metodo=MetodoPago.PLANILLA,
                comentarios="Procesado por Fast-Path (Planilla)"
            )
            return ParseResult(
                transaction=tx,
                es_ambiguo=False,
                opciones_categoria=[],
                es_ambiguo_metodo=False,
                opciones_metodo=[]
            )

    # 2. Fast-path estándar para concepto + monto o monto + concepto
    match = re.match(r'^\s*(?P<monto>\d+)\s+(?P<concepto>.+)\s*$', text_clean)
    if match:
        monto_str = match.group('monto')
        concepto_str = match.group('concepto').strip()
        
        # Buscar el concepto en nuestro diccionario de categorías
        for categoria, palabras in CATEGORIAS_DICT.items():
            if concepto_str in palabras:
                tx = Transaction(
                    id_transaccion=message_id,
                    fecha=get_local_date(),
                    tipo=TipoTransaccion.GASTO,
                    monto=Decimal(monto_str),
                    concepto=concepto_str.capitalize(),
                    categoria=categoria,
                    metodo=MetodoPago.DEBITO,
                    comentarios="Procesado por Fast-Path (Regex)"
                )
                return ParseResult(
                    transaction=tx,
                    es_ambiguo=False,
                    opciones_categoria=[],
                    es_ambiguo_metodo=False,
                    opciones_metodo=[]
                )
    return None

def get_system_prompt(categorias_disponibles: list[str]) -> str:
    hoy = get_local_date().isoformat()
    bot_context = os.getenv("BOT_CONTEXT", "Chile, usando pesos chilenos sin decimales.")
    return f"""
Eres un asistente financiero experto. Tu tarea es extraer información estructurada a partir de mensajes de texto coloquiales.
Debes devolver un JSON estricto que cumpla con el esquema requerido.

HOY ES: {hoy}. Usa esta fecha de referencia estricta para cálculos de tiempo como "ayer", "el martes pasado", "hace 3 días".
CONTEXTO DEL USUARIO Y MONEDA: {bot_context} (Ten muy en cuenta este contexto geográfico para entender modismos, nombres de tiendas y magnitudes).

Reglas de negocio:
1. 'es_transaccion': Evalúa si el texto relata un gasto o ingreso REAL Y PROPIO del usuario. Si es una historia sobre otra persona (ej. "mi amigo gastó...", "él me contó..."), una conversación general, o spam, marca esto como false.
2. 'monto': Debe ser un número entero o decimal positivo. Infiere la magnitud correcta según el contexto. Si es un reembolso, el monto sigue siendo positivo pero el tipo cambia.
3. 'tipo': Debe ser estrictamente "Ingreso" o "Gasto". (Si fue un Egreso, es gasto. Si dice "me pagaron", "sueldo", "reembolso", "devolución", es Ingreso).
4. 'concepto': Breve resumen de la transacción en 1 o 2 palabras (ej. "Uber", "Cerveza", "Sueldo", "Almuerzo Casino", "Taller Pádel").
5. 'categoria': DEBE ser EXACTAMENTE una de las siguientes opciones textuales: {categorias_disponibles}.
   - Determina la categoría según la naturaleza de la actividad o producto consumido (ej. comida o casino -> 'Alimentación', deportes o pádel o gimnasio -> 'Deportes', consultas o farmacia -> 'Salud', pasajes o viajes -> 'Transporte').
   - OJO: NUNCA fuerces 'Alimentación' solo porque el método sea 'Planilla'. La categoría depende del bien o servicio adquirido.
6. 'metodo': Debe ser "Débito", "Crédito", "Efectivo", "Transferencia", "Planilla", o "Otro".
   - Por defecto, si el usuario no menciona método de pago en compras cotidianas, asume "Débito".
   - METODO 'PLANILLA': Representa cualquier gasto o beneficio descontado directamente por liquidación de sueldo laboral.
     * Puede aplicar a cualquier categoría: casino laboral ('Alimentación'), talleres deportivos o gimnasio de la empresa ('Deportes'), seguro de salud complementario ('Salud'), etc.
     * Si el mensaje indica explícitamente descuento por planilla, marcar credencial, o casino de la pega/trabajo (ej. "por planilla", "descuento por planilla", "marqué credencial", "casino pega", "casino de la pega", "casino del trabajo"), asigna metodo="Planilla" y 'es_ambiguo_metodo'=false.
     * Si el mensaje indica explícitamente otro medio de pago (ej. "almorcé en el casino con débito", "pagué con crédito"), asigna ese método y 'es_ambiguo_metodo'=false.
     * AMBIGÜEDAD DÉBITO VS PLANILLA: Si el usuario menciona un consumo en el casino laboral pero NO aclara si lo pagó con débito o por planilla (ej. "3500 almuerzo casino", "4000 en el casino", "almorcé en el casino"), debes marcar 'es_ambiguo_metodo'=true, 'opciones_metodo'=["Planilla", "Débito"], y en 'metodo' pon "Planilla" por defecto.
7. 'fecha': Deduce la fecha exacta en formato YYYY-MM-DD. Si no hay referencia, asume {hoy}. Debes tener cuidado, la compra puede aludir al futuro pero haber ocurrido en el presente o pasado. Por ejemplo puedo comprar algo hoy para una actividad de la próxima semana.
8. 'comentarios': Opcional, guarda notas extra para entender el movimiento al revisar los datos.
9. 'es_ambiguo': Si el gasto puede encajar razonablemente en dos o más categorías distintas (por ejemplo, "4 lucas de helado" o "10k cervezas" pueden ser 'Alimentación' o 'Salidas', "regalo" puede ser 'Otros Gastos' o 'Mesada', "15k uber al estadio" puede ser 'Transporte' o 'Salidas'), debes marcar esto como true.
10. 'opciones_categoria': Si marcaste 'es_ambiguo' como true, debes enviar un arreglo con las 2 opciones de categoría más probables sacadas estrictamente de {categorias_disponibles}. Si es_ambiguo es false, devuelve un arreglo vacío.
11. 'es_ambiguo_metodo': true si hay duda sobre si el pago fue por 'Planilla' o 'Débito' (según la regla 6). En caso contrario, false.
12. 'opciones_metodo': Si 'es_ambiguo_metodo' es true, debe ser estrictamente ["Planilla", "Débito"]. Si es false, devuelve un arreglo vacío.
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
    es_ambiguo_metodo: bool = False
    opciones_metodo: list[str] = []

class MultiTransactionExtraction(BaseModel):
    """Esquema para extraer múltiples transacciones."""
    transacciones: list[TransactionExtraction]

def parse_transaction_message(text: str, message_id: str, categorias_disponibles: list[str] | None = None) -> ParseResult:
    """
    Parsea un mensaje de texto usando Regex o Gemini AI y devuelve un ParseResult.
    """
    if not categorias_disponibles:
        categorias_disponibles = list(CATEGORIAS_DICT.keys()) if CATEGORIAS_DICT else [
            "Alimentación", "Transporte", "Salidas", "Servicios Básicos", 
            "Salud", "Educación", "Otros Gastos", "Remuneraciones", "Inversiones"
        ]

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
        es_ambiguo_metodo = extracted_data.pop("es_ambiguo_metodo", False)
        opciones_metodo = extracted_data.pop("opciones_metodo", [])
        
        fecha_str = extracted_data.pop("fecha", None)
        fecha_tx = get_local_date()
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
        
        return ParseResult(
            transaction=tx,
            es_ambiguo=es_ambiguo,
            opciones_categoria=opciones_categoria,
            es_ambiguo_metodo=es_ambiguo_metodo,
            opciones_metodo=opciones_metodo
        )

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
    ESTA_SEMANA = "esta_semana"
    ULTIMOS_7_DIAS = "ultimos_7_dias"
    ULTIMOS_30_DIAS = "ultimos_30_dias"
    HOY = "hoy"
    AYER = "ayer"
    SIEMPRE = "siempre"
    PERSONALIZADO = "personalizado"

class IntentType(str, Enum):
    GASTO_TOTAL = "gasto_total"
    GASTO_PROMEDIO = "gasto_promedio"
    DESGLOSE_CATEGORIA = "desglose_categoria"
    DESGLOSE_METODO = "desglose_metodo"
    BUSQUEDA_ESPECIFICA = "busqueda_especifica"
    MAYOR_GASTO = "mayor_gasto"
    CONTEO = "conteo"

class AnalisisQuery(BaseModel):
    intent: IntentType = Field(..., description="Intención analítica de la consulta")
    filtro_tiempo: FiltroTiempo = Field(FiltroTiempo.ESTE_MES, description="Período temporal identificado")
    fecha_desde: str | None = Field(None, description="Fecha de inicio calculada en formato YYYY-MM-DD")
    fecha_hasta: str | None = Field(None, description="Fecha de fin calculada en formato YYYY-MM-DD")
    periodo_legible: str = Field("este mes", description="Descripción en lenguaje natural del período analizado (ej: 'este mes', 'esta semana', 'en agosto')")
    categoria_objetivo: str | None = Field(None, description="Categoría de presupuesto si la pregunta apunta a una (ej: 'Alimentación', 'Transporte', 'Salidas')")
    metodo_objetivo: str | None = Field(None, description="Método de pago si la pregunta especifica uno (ej: 'Planilla', 'Débito', 'Crédito')")
    terminos_busqueda: list[str] = Field(
        default_factory=list,
        description="Lista de sinónimos, lemas, sustantivos y verbos relacionados para buscar en conceptos/comentarios. Para 'almorzando'/'almorzar': ['almuerzo', 'almorzar', 'almorzando', 'casino', 'menu', 'colacion']. Para 'super': ['super', 'supermercado', 'lider', 'jumbo', 'tottus']. Para 'carrete': ['carrete', 'fiesta', 'bar', 'cerveza', 'copete']."
    )
    concepto_objetivo: str | None = Field(None, description="Término principal capitalizado para el título de la respuesta (ej: 'Almuerzo', 'Supermercado', 'Uber')")

def filtrar_transacciones(
    transacciones: list[dict],
    filtro_tiempo: FiltroTiempo,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None
) -> list[dict]:
    hoy = get_local_date()
    filtradas = []

    f_desde = None
    f_hasta = None
    if fecha_desde:
        try:
            f_desde = date.fromisoformat(fecha_desde.split("T")[0])
        except Exception:
            f_desde = None
    if fecha_hasta:
        try:
            f_hasta = date.fromisoformat(fecha_hasta.split("T")[0])
        except Exception:
            f_hasta = None

    for tx in transacciones:
        fecha_str = tx.get('fecha', '')
        if not fecha_str:
            continue

        try:
            if "T" in fecha_str:
                fecha_tx = date.fromisoformat(fecha_str.split("T")[0])
            else:
                fecha_tx = date.fromisoformat(fecha_str)
        except ValueError:
            continue

        # Si vienen fechas explícitas calculadas por el modelo, tienen prioridad
        if f_desde and fecha_tx < f_desde:
            continue
        if f_hasta and fecha_tx > f_hasta:
            continue

        # Filtros temporales estándar relativos
        if not f_desde and not f_hasta:
            if filtro_tiempo == FiltroTiempo.ESTE_MES:
                if not (fecha_tx.year == hoy.year and fecha_tx.month == hoy.month):
                    continue
            elif filtro_tiempo == FiltroTiempo.MES_PASADO:
                mes_pasado = hoy.month - 1 if hoy.month > 1 else 12
                año_pasado = hoy.year if hoy.month > 1 else hoy.year - 1
                if not (fecha_tx.year == año_pasado and fecha_tx.month == mes_pasado):
                    continue
            elif filtro_tiempo == FiltroTiempo.ESTE_AÑO:
                if fecha_tx.year != hoy.year:
                    continue
            elif filtro_tiempo == FiltroTiempo.HOY:
                if fecha_tx != hoy:
                    continue
            elif filtro_tiempo == FiltroTiempo.AYER:
                if fecha_tx != hoy - timedelta(days=1):
                    continue
            elif filtro_tiempo == FiltroTiempo.ULTIMOS_7_DIAS:
                if fecha_tx < hoy - timedelta(days=7):
                    continue
            elif filtro_tiempo == FiltroTiempo.ULTIMOS_30_DIAS:
                if fecha_tx < hoy - timedelta(days=30):
                    continue
            elif filtro_tiempo == FiltroTiempo.ESTA_SEMANA:
                lunes = hoy - timedelta(days=hoy.weekday())
                if fecha_tx < lunes:
                    continue

        filtradas.append(tx)

    return filtradas

def responder_consulta_natural(pregunta: str, transacciones: list[dict]) -> str:
    """
    Motor analítico semántico y determinista.
    Usa Gemini para planificar la consulta con sinónimos, lemas y filtros temporales,
    y Python para calcular el resultado riguroso matemáticamente sin alucinaciones numéricas.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("La variable GEMINI_API_KEY no está configurada.")

    client = genai.Client(api_key=api_key)
    hoy_date = get_local_date()
    hoy = hoy_date.isoformat()

    prompt_router = f"""
    Hoy es {hoy} ({hoy_date.strftime('%A')}).
    Eres el clasificador del motor analítico financiero de una persona en Chile.
    Analiza la pregunta del usuario y extrae los parámetros de búsqueda con máxima flexibilidad semántica:

    1. 'intent':
       - 'busqueda_especifica': consultas sobre un concepto, actividad, comercio o ítem específico (ej: almuerzo, almorzando, uber, super, helado, sushi, bencina, cervezas).
       - 'gasto_total': suma general o de una categoría completa sin concepto puntual (ej: 'cuánto gasté este mes', 'cuánto gasté en transporte').
       - 'gasto_promedio': promedio por día o período (ej: 'cuánto gasto al día').
       - 'desglose_categoria': desglose o ranking de gastos por categoría.
       - 'desglose_metodo': desglose por método de pago (débito, crédito, planilla).
       - 'mayor_gasto': el gasto más alto o compra más cara (ej: 'cuál fue mi gasto más caro', 'en qué gasté más').
       - 'conteo': cantidad de veces o frecuencia (ej: 'cuántas veces pedí delivery', 'cuántas veces fui al cine').

    2. Regla Crucial de Lematización en 'terminos_busqueda':
       Para cualquier búsqueda de concepto o actividad, genera una lista exhaustiva de sinónimos, sustantivos y lemas:
       - Si el usuario usa un verbo o gerundio (ej: 'almorzando', 'almorzar'), incluye ['almuerzo', 'almorzar', 'almorzando', 'casino', 'menu', 'colacion', 'lunch'].
       - Si dice 'tomando', 'carreteando' o 'saliendo' -> ['carrete', 'bar', 'cerveza', 'copete', 'fiesta', 'junta'].
       - Si dice 'viajando' o 'moviéndome' -> ['uber', 'didi', 'cabify', 'metro', 'bip', 'pasaje', 'viaje', 'taxi'].
       - Si dice 'super' o 'comprando comida' -> ['super', 'supermercado', 'lider', 'jumbo', 'tottus', 'santa isabel', 'unimarc'].
       - En 'concepto_objetivo', pon el sustantivo canónico en mayúscula inicial (ej: 'Almuerzo', 'Uber', 'Supermercado', 'Bencina').

    3. Rango de Fechas:
       - Calcula 'fecha_desde' y 'fecha_hasta' relativas a hoy ({hoy}).
       - 'esta semana': lunes de esta semana ({ (hoy_date - timedelta(days=hoy_date.weekday())).isoformat() }) hasta hoy.
       - 'este mes': desde {hoy_date.strftime('%Y-%m-01')} hasta hoy.
       - 'mes pasado': primer y último día del mes pasado.
       - 'periodo_legible': ej. 'este mes', 'esta semana', 'los últimos 7 días', 'en agosto'.
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
        
        intent_str = query_data.get("intent")
        try:
            intent = IntentType(intent_str)
        except ValueError:
            intent = IntentType.BUSQUEDA_ESPECIFICA

        filtro_tiempo_str = query_data.get("filtro_tiempo", "este_mes")
        try:
            filtro_tiempo = FiltroTiempo(filtro_tiempo_str)
        except ValueError:
            filtro_tiempo = FiltroTiempo.ESTE_MES

        fecha_desde = query_data.get("fecha_desde")
        fecha_hasta = query_data.get("fecha_hasta")
        periodo_desc = query_data.get("periodo_legible") or filtro_tiempo.value.replace('_', ' ')
        categoria_obj = query_data.get("categoria_objetivo")
        metodo_obj = query_data.get("metodo_objetivo")
        concepto_obj = query_data.get("concepto_objetivo")
        terminos_busqueda = query_data.get("terminos_busqueda") or []

        # 1. Filtrado temporal
        txs_filtradas = filtrar_transacciones(
            transacciones,
            filtro_tiempo=filtro_tiempo,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta
        )

        # 2. Normalización de términos de búsqueda
        terminos_norm = []
        for t in terminos_busqueda:
            if t and isinstance(t, str) and t.strip():
                tn = quitar_acentos(t.strip().lower())
                if tn not in terminos_norm:
                    terminos_norm.append(tn)
        if concepto_obj:
            cn = quitar_acentos(concepto_obj.strip().lower())
            if cn and cn not in terminos_norm:
                terminos_norm.append(cn)

        # Helper para el neteo matemático
        categorias_ingreso = ["Remuneraciones", "Otros Ingresos", "Inversiones"]

        def get_monto_neto(tx) -> Decimal:
            m = Decimal(str(tx.get('monto', 0)).replace(',', '').replace('$', ''))
            tipo = str(tx.get('tipo', '')).lower()
            cat = str(tx.get('categoria', ''))
            is_ingreso_nativo = cat in categorias_ingreso

            if tipo == "gasto":
                return -m if is_ingreso_nativo else m
            elif tipo == "ingreso":
                return m if is_ingreso_nativo else -m
            return m

        # 3. Predicado de coincidencia flexible
        def match_tx(tx: dict) -> bool:
            # Filtro por método de pago si fue solicitado
            if metodo_obj:
                m_tx = quitar_acentos(str(tx.get('metodo', '')).lower())
                m_req = quitar_acentos(metodo_obj.lower())
                if m_req not in m_tx:
                    return False

            # Filtro por categoría estricta solo si NO hay términos de búsqueda semánticos
            cat_tx = quitar_acentos(str(tx.get('categoria', '')).lower())
            if categoria_obj and not terminos_norm:
                cat_req = quitar_acentos(categoria_obj.lower())
                if cat_req not in cat_tx:
                    return False

            # Filtro semántico por lemas, sinónimos o conceptos
            if terminos_norm:
                concepto_tx = quitar_acentos(str(tx.get('concepto', '')).lower())
                com_tx = quitar_acentos(str(tx.get('comentarios', '')).lower())
                texto_combinado = f"{concepto_tx} {cat_tx} {com_tx}"

                matched = False
                for t in terminos_norm:
                    if t in texto_combinado:
                        matched = True
                        break
                    # Coincidencia flexible de raíz de palabras (prefijos >= 4 caracteres)
                    palabras = concepto_tx.split() + com_tx.split()
                    for p in palabras:
                        if len(t) >= 4 and len(p) >= 4:
                            if p.startswith(t[:4]) or t.startswith(p[:4]):
                                matched = True
                                break
                    if matched:
                        break
                if not matched:
                    return False

            return True

        analisis_txs = [tx for tx in txs_filtradas if match_tx(tx)]

        respuesta_final = ""

        # --- RAMA 1: MAYOR GASTO ---
        if intent == IntentType.MAYOR_GASTO:
            gastos = [tx for tx in analisis_txs if str(tx.get('tipo', '')).lower() == 'gasto' and get_monto_neto(tx) > 0]
            if not gastos:
                respuesta_final = f"ℹ️ No encontré registros de gastos en el período indicado ({periodo_desc})."
            else:
                top_gastos = sorted(gastos, key=lambda x: get_monto_neto(x), reverse=True)[:3]
                mayor = top_gastos[0]
                m_txt = f" por {mayor.get('metodo')}" if mayor.get('metodo') else ""
                respuesta_final = (
                    f"🏆 **Mayor Gasto ({periodo_desc}):**\n"
                    f"• {str(mayor.get('fecha', '')).split('T')[0]}: **{format_currency(get_monto_neto(mayor))}** en *{mayor.get('concepto', '')}* "
                    f"({mayor.get('categoria', '')}{m_txt})\n"
                )
                if len(top_gastos) > 1:
                    respuesta_final += "\nOtros gastos significativos:\n"
                    for g in top_gastos[1:]:
                        f_str = str(g.get('fecha', '')).split('T')[0]
                        met_str = f" - {g.get('metodo')}" if g.get('metodo') else ""
                        respuesta_final += f"• {f_str}: {format_currency(get_monto_neto(g))} ({g.get('concepto', '')}{met_str})\n"

        # --- RAMA 2: CONTEO O FRECUENCIA ---
        elif intent == IntentType.CONTEO:
            veces = len(analisis_txs)
            total = sum(get_monto_neto(tx) for tx in analisis_txs)
            titulo = concepto_obj or categoria_obj or (terminos_busqueda[0] if terminos_busqueda else "esto")
            if veces == 0:
                respuesta_final = f"🔎 No registraste transacciones de '{titulo}' ({periodo_desc})."
            else:
                prom_str = f" (promedio de {format_currency(total / Decimal(veces))} por vez)" if veces > 1 else ""
                respuesta_final = (
                    f"🔢 **Frecuencia de '{titulo.capitalize()}' ({periodo_desc}):**\n"
                    f"Has registrado esto **{veces} veces**, sumando un total de **{format_currency(total)}**{prom_str}."
                )

        # --- RAMA 3: BÚSQUEDA ESPECÍFICA DE CONCEPTO / COMERCIO ---
        elif intent == IntentType.BUSQUEDA_ESPECIFICA or terminos_norm:
            veces = len(analisis_txs)
            total = sum(get_monto_neto(tx) for tx in analisis_txs)
            titulo = concepto_obj or (categoria_obj if categoria_obj else (terminos_busqueda[0] if terminos_busqueda else "Búsqueda"))

            if veces == 0:
                respuesta_final = f"🔎 No encontré gastos relacionados a '{titulo}' ({periodo_desc})."
            else:
                prom_str = f" (promedio de {format_currency(total / Decimal(veces))} por vez)" if veces > 1 else ""
                respuesta_final = (
                    f"🔎 **Búsqueda: '{titulo.capitalize()}' ({periodo_desc})**\n"
                    f"Has registrado gastos en esto **{veces} veces**, sumando un total de **{format_currency(total)}**{prom_str}."
                )

                ultimos = sorted(analisis_txs, key=lambda x: str(x.get('fecha', '')), reverse=True)[:5]
                if ultimos:
                    respuesta_final += "\n\nÚltimos registros:\n"
                    for tx in ultimos:
                        fecha = str(tx.get('fecha', '')).split('T')[0]
                        monto_tx = get_monto_neto(tx)
                        metodo_str = f" - {tx.get('metodo')}" if tx.get('metodo') else ""
                        respuesta_final += f"• {fecha}: {format_currency(monto_tx)} ({tx.get('concepto', '')}{metodo_str})\n"

        # --- RAMA 4: GASTO TOTAL ---
        elif intent == IntentType.GASTO_TOTAL:
            # Excluir ingresos nativos por defecto
            gastos_puros = [tx for tx in analisis_txs if tx.get('categoria', '') not in categorias_ingreso]
            total = sum(get_monto_neto(tx) for tx in gastos_puros)
            cat_str = f" en {categoria_obj}" if categoria_obj else ""
            met_str = f" con {metodo_obj}" if metodo_obj else ""
            respuesta_final = f"📊 Tu gasto total{cat_str}{met_str} ({periodo_desc}) es de **{format_currency(total)}** ({len(gastos_puros)} transacciones)."

        # --- RAMA 5: GASTO PROMEDIO ---
        elif intent == IntentType.GASTO_PROMEDIO:
            gastos_puros = [tx for tx in analisis_txs if tx.get('categoria', '') not in categorias_ingreso]
            total = sum(get_monto_neto(tx) for tx in gastos_puros)
            
            import calendar
            dias = 1
            if fecha_desde and fecha_hasta:
                try:
                    dias = max(1, (date.fromisoformat(fecha_hasta) - date.fromisoformat(fecha_desde)).days + 1)
                except Exception:
                    dias = hoy_date.day
            elif filtro_tiempo == FiltroTiempo.ESTE_MES:
                dias = hoy_date.day
            elif filtro_tiempo == FiltroTiempo.MES_PASADO:
                mes_pasado = hoy_date.month - 1 if hoy_date.month > 1 else 12
                año_pasado = hoy_date.year if hoy_date.month > 1 else hoy_date.year - 1
                dias = calendar.monthrange(año_pasado, mes_pasado)[1]
            elif filtro_tiempo == FiltroTiempo.ESTE_AÑO:
                dias = (hoy_date - date(hoy_date.year, 1, 1)).days + 1
            elif filtro_tiempo in [FiltroTiempo.ESTA_SEMANA, FiltroTiempo.ULTIMOS_7_DIAS]:
                dias = 7
            else:
                dias = max(1, hoy_date.day)

            promedio = total / Decimal(dias)
            cat_str = f" en {categoria_obj}" if categoria_obj else ""
            respuesta_final = f"📉 **Promedio Diario{cat_str} ({periodo_desc}):**\nHas gastado una media de **{format_currency(promedio)} al día** ({dias} días analizados)."

        # --- RAMA 6: DESGLOSE POR MÉTODO ---
        elif intent == IntentType.DESGLOSE_METODO:
            desglose = defaultdict(Decimal)
            for tx in analisis_txs:
                metodo = tx.get('metodo', 'Desconocido')
                desglose[metodo] += get_monto_neto(tx)

            respuesta_final = f"💳 **Desglose por Método de Pago ({periodo_desc}):**\n"
            total = Decimal(0)
            for m, monto in sorted(desglose.items(), key=lambda x: x[1], reverse=True):
                respuesta_final += f"• {m}: {format_currency(monto)}\n"
                total += monto
            respuesta_final += f"\n**Total:** {format_currency(total)}"

        # --- RAMA 7: DESGLOSE POR CATEGORÍA ---
        elif intent == IntentType.DESGLOSE_CATEGORIA:
            desglose = defaultdict(Decimal)
            cat_obj_norm = quitar_acentos(categoria_obj.lower()) if categoria_obj else ""
            for tx in analisis_txs:
                cat = tx.get('categoria', 'Sin Categoría')
                if cat_obj_norm and cat_obj_norm not in quitar_acentos(cat.lower()):
                    continue
                desglose[cat] += get_monto_neto(tx)

            respuesta_final = f"📁 **Desglose por Categoría ({periodo_desc}):**\n"
            total = Decimal(0)
            for c, monto in sorted(desglose.items(), key=lambda x: x[1], reverse=True):
                pct = (monto / total * Decimal(100)) if total > 0 else Decimal(0)
                respuesta_final += f"• {c}: {format_currency(monto)}\n"
                total += monto
            respuesta_final += f"\n**Total analizado:** {format_currency(total)}"

            if total > 0:
                try:
                    prompt_opinion = f"Acabo de calcular este gasto: {respuesta_final}. Dame 1 frase amable, amistosa y súper breve opinando sobre esta distribución de gastos. No des formato markdown."
                    chat_op = client.chats.create(model='gemini-flash-lite-latest')
                    opinion = chat_op.send_message(prompt_opinion)
                    respuesta_final += f"\n\n🤖 _{opinion.text.strip()}_"
                except Exception:
                    pass

        else:
            respuesta_final = "🔎 Entendí tu consulta, pero no encontré suficientes registros coincidentes en tu historial. ¡Prueba preguntarme por sumas específicas como 'cuánto he gastado en uber' o 'cuánto gasté en almuerzo'!"

        return respuesta_final
        
    except Exception as e:
        logger.error(f"Error en consulta natural determinista: {e}")
        raise ValueError("Lo siento, mis circuitos analíticos fallaron al clasificar tu intención. Intenta nuevamente.")

ANGULOS_COMICOS = [
    "Celebración cómplice (valida el gusto o la ocasión con alegría y picardía, luego contrarrestando con un comentario incentivando el ahorro).",
    "Humor de autocuidado y recompensa (trata el gasto con cariño como un premio bien ganado a la rutina, bromeando sobre darse lujos merecidos).",
    "Ironía liviana y simpática (una broma sobre cómo la tentación nos gana a todos, sin generar demasiada culpa ni juzgar con dureza).",
    "Complicidad de 'ya fue, nada que hacer, mejor disfrútalo' (asumir la compra con buen ánimo y resignación, enfocándose en disfrutar el momento).",
    "Optimismo financiero relajado (bromear con eventual futura suerte en el azar o lotería y que lo importante es el equilibrio general, no privarse de todo en la vida)."
]

def generar_comentario_ironico(
    monto: Decimal,
    concepto: str,
    categoria: str,
    estado_presupuesto: str | None = None,
    es_anomalo: bool = False,
    temperature: float | None = None,
    angulo: str | None = None
) -> str:
    """
    Genera un comentario breve, proporcionado e irónico sobre una transacción recién registrada.
    
    Args:
        monto: Monto de la transacción en Decimal.
        concepto: Descripción del gasto o ingreso.
        categoria: Categoría de la transacción.
        estado_presupuesto: Alerta de presupuesto mensual si aplica.
        es_anomalo: Si el gasto sobrepasa significativamente la media histórica.
        temperature: Temperatura para controlar creatividad (default: lee GEMINI_TEMPERATURE o 0.9).
        angulo: Enfoque cómico específico opcional. Si es None, se escoge uno con probabilidad 0.75 (el 25% se omite).
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return ""
        
    client = genai.Client(api_key=api_key)
    
    bot_context = os.getenv("BOT_CONTEXT", "Chile, usando pesos chilenos sin decimales.")
    bot_tone = os.getenv("BOT_TONE", "crítico y fiscalizador, pero constructivo y amable, con una personalidad buena para los chistes y liviana")

    if temperature is None:
        try:
            raw_temp = os.getenv("GEMINI_TEMPERATURE", os.getenv("BOT_TEMPERATURE", os.getenv("TEMPERATURE", "0.9")))
            temperature = float(raw_temp)
        except (ValueError, TypeError):
            temperature = 0.9
            
    if angulo is not None:
        angulo_seleccionado = angulo
    else:
        # Probabilidad de 0.6 de sugerir un ángulo cómico; 0.25 de omitirlo para variedad orgánica
        angulo_seleccionado = random.choice(ANGULOS_COMICOS) if random.random() < 0.6 else None
    
    presupuesto_str = f"\nOJO, DATO VITAL: {estado_presupuesto} Ten en cuenta esto en tu comentario si está cerca de pasarse o ya se pasó de su límite mensual.\n" if estado_presupuesto else ""
    
    anomalia_str = ""
    if es_anomalo:
        anomalia_str = "\n🚨 ¡ALERTA ANOMALÍA! Con este último registro, el usuario acaba de gastar mucho más de lo que gasta normalmente en un mes en esta categoría (superó su promedio histórico + 50%). Céntrate en esto: dale una ADVERTENCIA SERIA. No uses humor burlón para esta alerta, sé más constructivo pero mantén tu rol de fiscalizador.\n"

    system_instruction = (
        f"Eres un amigo/a chileno/a cercano/a {bot_tone} que acompaña al usuario fiscalizando sus finanzas con humor, y empatía crítica adecuada a su contexto.\n"
        f"Contexto geográfico y monetario: {bot_context}\n\n"
        "LENGUAJE Y VOCABULARIO CHILENO\n"
        "- Habla como un amigo/a chileno/a real con naturalidad, evitando un vocabulario forzado, sin saturar con modismos.\n"
        "- Usa modismos chilenos naturales y limpios: 'lucas', 'gustito', 'flojera' o 'lata', 'micro', 'andar pato', 'salir salado', 'ojo al charqui', 'bajón', 'hacerse el larry', 'ya fue', 'filo', 'la dura', 'el pique', 'el taco'.\n"
        "- PROHIBIDO el vocabulario neutro de doblaje o foráneo: nada de 'pereza','subte', 'chaval', 'lana', 'plática', 'pana', 'nevera' ni 'ordenador'.\n"
        "- Usa entonación chilena relajada y cotidiana (ej: 'buena po', 'la hiciste corta', 'igual aperraste', 'mañana toca compensar', entre otros).\n\n"
        "FILOSOFÍA Y ESPÍRITU DEL BOT:\n"
        "- Tu objetivo es acompañar al usuario con un toque de humor pícaro y entretenido, NUNCA hacerlo sentir culpable, tacaño o mal por gastar su propia plata. Puedes ser crítico pero siempre con empatía.\n"
        "- Valida las cosas lindas y humanas: si el gasto es una celebración (como un logro académico, cumpleaños, aniversario, etc.), un regalo a un ser querido, un gusto bien ganado o un momento para compartir, ¡celébralo con alegría y buena onda! Sin olvidar el rol fiscalizador\n"
        "- Critica los gastos excesivos, evitables o cómodos con ironía y humor. No seas complaciente con el usuario pero no lo hagas sentirse mala persona.\n"
        "SENTIDO DE PROPORCIÓN ECONÓMICA (CLP):\n"
        "- Micro-gasto (< $5.000): Cosas cotidianas (café, snack, pasaje). Tómatelo con total normalidad; una broma simpática sobre los pequeños placeres diarios.\n"
        "- Gasto habitual / moderado ($5.000 - $30.000): Salidas, comida rica, regalos piola, farmacia. Dinero estándar. Reconoce el gusto o la ocasión y tira una broma ligera de apoyo.\n"
        "- Gasto medio ($30.000 - $80.000): Salida especial, compras mayores. Bromea con estilo sobre darse lujos de magnate, deseándole que lo disfrute al máximo.\n"
        "- Gasto fuerte (> $80.000): Compras importantes. Aquí sí cabe un recordatorio amistoso de fiscalizador atento para sugerir cuidar la billetera en lo que queda de mes, pero siempre con afecto y humor.\n\n"
        "REGLAS ESTRICTAS DE ESTILO:\n"
        "1. EVITAR hacer sentir culpable o mal al usuario (NADA de decir que 'botó la plata', que 'sus ahorros lo odian' o juzgarlo con dureza). Sé el amigo bueno para la talla pero fiscalizador y que incentiva el ahorro.\n"
        "2. PROHIBIDO el lenguaje soez, vulgar o con garabatos (NADA de 'mierda', 'aweonao', etc.). Mantén el humor pícaro, cálido, liviano y chispeante.\n"
        "3. EVITAR empezar tu respuesta repitiendo el monto y concepto (NADA de '¿X lucas en Y?' ni 'X lucas en Y...'). Entra directo a la idea o al comentario.\n"
        "4. EVITAR la muletilla cliché de 'a fin de mes vas a comer tierra/aire'. Sé creativo con situaciones cotidianas y optimistas.\n"
        "5. Sé conciso: idealmente 2 oraciones breves, como máximo 3.\n"
        "6. Agrega un ÚNICO emoji al final de tu comentario. Entrega solo texto plano sin formato markdown."
    )

    angulo_linea = f"Enfoque cómico sugerido para variar tu respuesta: {angulo_seleccionado}\n. No seas rígido con el ángulo, es una sugerencia" if angulo_seleccionado else ""

    user_prompt = (
        f"Acabo de gastar {format_currency(monto)} en '{concepto}' (Categoría: {categoria}).\n"
        f"{presupuesto_str}{anomalia_str}"
        f"{angulo_linea}"
        "Escribe tu comentario breve de 1 o 2 oraciones, máximo 3:"
    )
    
    try:
        chat = client.chats.create(
            model='gemini-flash-lite-latest',
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature
            )
        )
        response = chat.send_message(user_prompt)
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
            _ = tx_data.pop("es_ambiguo_metodo", False)
            _ = tx_data.pop("opciones_metodo", [])
            
            fecha_str = tx_data.pop("fecha", None)
            fecha_tx = get_local_date()
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
