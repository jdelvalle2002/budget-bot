import os
import logging
import httpx
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

from src.parser import parse_transaction_message
from src.sheets_client import GoogleSheetsClient

# Cargar variables de entorno
load_dotenv()

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Budget Bot API")

ALLOWED_USER_ID = os.getenv("USER_ID")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Instancia global del cliente de sheets (idealmente usar inyección de dependencias)
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

@app.post(f"/webhook/{TELEGRAM_TOKEN}")
async def telegram_webhook(request: Request):
    """
    Endpoint para recibir actualizaciones de Telegram.
    La ruta incluye el token por seguridad, para que no sea predecible.
    """
    if not sheets_client:
        raise HTTPException(status_code=500, detail="El cliente de Google Sheets no está configurado.")

    update = await request.json()
    
    # Extraer datos básicos del update de Telegram
    message = update.get("message")
    if not message:
        return {"status": "ignored", "reason": "Not a message update"}
        
    chat_id = str(message.get("chat", {}).get("id"))
    text = message.get("text", "")
    message_id = str(message.get("message_id"))

    # Validación Estricta de Seguridad
    if chat_id != ALLOWED_USER_ID:
        logger.warning(f"Acceso denegado. Intento desde chat_id: {chat_id}")
        # Retornamos 200 para que Telegram no reintente, pero internamente bloqueamos
        return {"status": "forbidden"}

    if not text:
        return {"status": "ignored", "reason": "Empty text"}

    logger.info(f"Procesando mensaje: '{text}' (ID: {message_id})")

    try:
        # 1. Parsear el mensaje
        transaction = parse_transaction_message(text, message_id=message_id)
        
        # 2. Insertar en Google Sheets (Idempotente)
        success = sheets_client.append_transaction(transaction)
        
        if success:
            logger.info("Transacción procesada y guardada con éxito.")
            # 3. Responder al usuario en Telegram
            respuesta = f"✅ Registrado exitosamente:\n- *Monto:* ${transaction.monto:,.0f}\n- *Categoría:* {transaction.categoria}\n- *Tipo:* {transaction.tipo.value}"
            await enviar_mensaje_telegram(chat_id, respuesta)
            
            return {"status": "ok", "transaction": transaction.model_dump()}
        else:
            logger.error("Falló la inserción en Google Sheets.")
            await enviar_mensaje_telegram(chat_id, "❌ Error guardando en Google Sheets.")
            raise HTTPException(status_code=500, detail="Error saving to Google Sheets")
            
    except ValueError as ve:
        logger.error(f"Error de validación/parseo: {ve}")
        await enviar_mensaje_telegram(chat_id, f"⚠️ No pude entender el mensaje:\n_{ve}_")
        return {"status": "error", "message": str(ve)}
    except Exception as e:
        logger.error(f"Error inesperado: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/health")
def health_check():
    return {"status": "healthy"}
