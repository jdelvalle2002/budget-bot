import os
import logging
import httpx
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from dotenv import load_dotenv

from src.parser import parse_transaction_message
from src.sheets_client import GoogleSheetsClient
from src.state import get_user_session, UserState

# Cargar variables de entorno
load_dotenv()

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Budget Bot API")

ALLOWED_USER_ID = os.getenv("USER_ID")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Instancia global del cliente de sheets
try:
    sheets_client = GoogleSheetsClient()
except Exception as e:
    logger.error(f"No se pudo inicializar GoogleSheetsClient: {e}")
    sheets_client = None

async def enviar_mensaje_telegram(chat_id: str, texto: str):
    """Función auxiliar para responderle al usuario vía Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown"
    }
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json=payload)
        except Exception as e:
            logger.error(f"Error enviando mensaje a Telegram: {e}")

async def process_telegram_update(chat_id: str, text: str, message_id: str):
    """
    Lógica conversacional que se ejecuta en Background para no bloquear a Telegram.
    """
    session = get_user_session(int(chat_id))

    # --- FLUJO 1: ESPERANDO CONFIRMACIÓN ---
    if session.state == UserState.AWAITING_CONFIRMATION:
        categoria_elegida = None
        # Buscar si el texto del usuario coincide con alguna opción
        for op in session.options:
            if op.lower() in text.lower():
                categoria_elegida = op
                break
        
        # Fallback si respondió "1" o "2"
        if not categoria_elegida:
            if text.strip() == "1" and len(session.options) >= 1:
                categoria_elegida = session.options[0]
            elif text.strip() == "2" and len(session.options) >= 2:
                categoria_elegida = session.options[1]

        if categoria_elegida:
            # Completar la transacción y guardar
            session.pending_transaction.categoria = categoria_elegida
            success = sheets_client.append_transaction(session.pending_transaction)
            if success:
                await enviar_mensaje_telegram(chat_id, f"✅ Registrado exitosamente en la categoría *{categoria_elegida}*.")
            else:
                await enviar_mensaje_telegram(chat_id, "❌ Error guardando en Google Sheets.")
            
            # Limpiar el estado de la conversación
            session.state = UserState.IDLE
            session.pending_transaction = None
            session.options = []
        else:
            opciones_str = " o ".join([f"*{op}*" for op in session.options])
            await enviar_mensaje_telegram(chat_id, f"⚠️ No entendí tu respuesta. Por favor responde {opciones_str}.")
        
        return

    # --- FLUJO 2: MENSAJE NUEVO ---
    try:
        parse_result = parse_transaction_message(text, message_id=message_id)
        
        if parse_result.es_ambiguo and parse_result.opciones_categoria:
            # Iniciar flujo de confirmación
            session.state = UserState.AWAITING_CONFIRMATION
            session.pending_transaction = parse_result.transaction
            session.options = parse_result.opciones_categoria
            
            opciones_list = "\n".join([f"{i+1}. {op}" for i, op in enumerate(session.options)])
            
            pregunta = (
                f"🤔 Parece que gastaste ${parse_result.transaction.monto:,.0f} en '{parse_result.transaction.concepto}'.\n"
                f"No estoy seguro de la categoría. ¿Cuál es?\n"
                f"{opciones_list}\n"
                f"_(Responde con el número o el nombre de la categoría)_"
            )
            await enviar_mensaje_telegram(chat_id, pregunta)
            
        else:
            # Procesamiento directo
            success = sheets_client.append_transaction(parse_result.transaction)
            if success:
                respuesta = f"✅ Registrado exitosamente:\n- *Monto:* ${parse_result.transaction.monto:,.0f}\n- *Categoría:* {parse_result.transaction.categoria}\n- *Tipo:* {parse_result.transaction.tipo.value}"
                await enviar_mensaje_telegram(chat_id, respuesta)
            else:
                await enviar_mensaje_telegram(chat_id, "❌ Error guardando en Google Sheets.")
                
    except ValueError as ve:
        logger.error(f"Error de validación/parseo: {ve}")
        await enviar_mensaje_telegram(chat_id, f"⚠️ No pude entender el mensaje:\n_{ve}_")
    except Exception as e:
        logger.error(f"Error inesperado: {e}")
        await enviar_mensaje_telegram(chat_id, "❌ Error interno del servidor procesando tu mensaje.")

@app.post(f"/webhook/{TELEGRAM_TOKEN}")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Endpoint para recibir actualizaciones de Telegram. (Devuelve HTTP 200 Inmediatamente).
    """
    if not sheets_client:
        raise HTTPException(status_code=500, detail="El cliente de Google Sheets no está configurado.")

    update = await request.json()
    message = update.get("message")
    
    if not message:
        return {"status": "ignored", "reason": "Not a message update"}
        
    chat_id = str(message.get("chat", {}).get("id"))
    text = message.get("text", "")
    message_id = str(message.get("message_id"))

    # Validación Estricta de Seguridad
    if chat_id != ALLOWED_USER_ID:
        logger.warning(f"Acceso denegado. Intento desde chat_id: {chat_id}")
        return {"status": "forbidden"}

    if not text:
        return {"status": "ignored", "reason": "Empty text"}

    logger.info(f"Recibido webhook de Telegram: '{text}' (ID: {message_id}). Procesando en Background...")
    
    # Enviar a BackgroundTasks para no bloquear el Webhook
    background_tasks.add_task(process_telegram_update, chat_id, text, message_id)
    
    return {"status": "ok"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}
