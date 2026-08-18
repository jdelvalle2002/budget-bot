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

async def enviar_mensaje_telegram(chat_id: str, texto: str, reply_markup: dict = None):
    """Función auxiliar para responderle al usuario vía Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
        
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json=payload)
        except Exception as e:
            logger.error(f"Error enviando mensaje a Telegram: {e}")

async def enviar_foto_telegram(chat_id: str, photo_bytes: bytes, caption: str = ""):
    """Función auxiliar para enviar imágenes a Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    data = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "Markdown"
    }
    files = {
        "photo": ("resumen.png", photo_bytes, "image/png")
    }
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, data=data, files=files)
        except Exception as e:
            logger.error(f"Error enviando foto a Telegram: {e}")

async def process_telegram_callback(chat_id: str, callback_data: str):
    """Maneja los clics en los botones Inline de Telegram"""
    session = get_user_session(int(chat_id))
    
    if callback_data.startswith("delete:"):
        tx_id = callback_data.split(":")[1]
        success = sheets_client.delete_transaction(tx_id)
        if success:
            await enviar_mensaje_telegram(chat_id, "🗑️ ✅ Transacción eliminada de Google Sheets exitosamente.")
        else:
            await enviar_mensaje_telegram(chat_id, "❌ Error al intentar eliminar la transacción. Puede que ya no exista.")
            
    elif callback_data.startswith("edit:"):
        tx_id = callback_data.split(":")[1]
        session.state = UserState.AWAITING_EDIT
        session.edit_transaction_id = tx_id
        
        instrucciones = (
            "✏️ *Modo Edición Activado*\n\n"
            "Escribe la corrección como si fuera un gasto nuevo.\n"
            "Ejemplo: si te equivocaste en el monto, escribe _'fueron 12000 en uber en verdad'_.\n\n"
            "Yo actualizaré el registro original."
        )
        await enviar_mensaje_telegram(chat_id, instrucciones)

async def process_telegram_update(chat_id: str, text: str, message_id: str):
    """
    Lógica conversacional que se ejecuta en Background para no bloquear a Telegram.
    """
    session = get_user_session(int(chat_id))

    # --- FLUJO 1: ESPERANDO CONFIRMACIÓN DE CATEGORÍA ---
    if session.state == UserState.AWAITING_CONFIRMATION:
        categoria_elegida = None
        for op in session.options:
            if op.lower() in text.lower():
                categoria_elegida = op
                break
        
        if not categoria_elegida:
            if text.strip() == "1" and len(session.options) >= 1:
                categoria_elegida = session.options[0]
            elif text.strip() == "2" and len(session.options) >= 2:
                categoria_elegida = session.options[1]

        if categoria_elegida:
            session.pending_transaction.categoria = categoria_elegida
            
            # Verificar si esto era parte de una edición
            if session.edit_transaction_id:
                session.pending_transaction.id_transaccion = session.edit_transaction_id
                success = sheets_client.update_transaction(session.pending_transaction)
                msg_exito = f"✅ Edición guardada exitosamente en la categoría *{categoria_elegida}*."
            else:
                success = sheets_client.append_transaction(session.pending_transaction)
                msg_exito = f"✅ Registrado exitosamente en la categoría *{categoria_elegida}*."
                
            if success:
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {"text": "✏️ Editar", "callback_data": f"edit:{session.pending_transaction.id_transaccion}"},
                            {"text": "🗑️ Deshacer", "callback_data": f"delete:{session.pending_transaction.id_transaccion}"}
                        ]
                    ]
                }
                await enviar_mensaje_telegram(chat_id, msg_exito, reply_markup=reply_markup)
            else:
                await enviar_mensaje_telegram(chat_id, "❌ Error guardando en Google Sheets.")
        else:
            await enviar_mensaje_telegram(chat_id, "❌ Operación cancelada. Puedes escribir tu gasto de nuevo.")
            
        # Limpiar estado en ambos casos (éxito o cancelación)
        session.state = UserState.IDLE
        session.pending_transaction = None
        session.options = []
        session.edit_transaction_id = None
        
        return

    # --- FLUJO 2: EDITANDO UNA TRANSACCIÓN (TEXTO LIBRE) ---
    if session.state == UserState.AWAITING_EDIT:
        try:
            # Parseamos usando el ID antiguo para sobrescribir
            parse_result = parse_transaction_message(text, message_id=session.edit_transaction_id)
            
            if parse_result.es_ambiguo and parse_result.opciones_categoria:
                session.state = UserState.AWAITING_CONFIRMATION
                session.pending_transaction = parse_result.transaction
                session.options = parse_result.opciones_categoria
                
                opciones_list = "\n".join([f"{i+1}. {op}" for i, op in enumerate(session.options)])
                pregunta = (
                    f"🤔 Parece que la corrección es por ${parse_result.transaction.monto:,.0f} en '{parse_result.transaction.concepto}'.\n"
                    f"No estoy seguro de la categoría. ¿Cuál es?\n"
                    f"{opciones_list}\n"
                    f"_(Responde con el número, el nombre de la categoría, o cualquier otra cosa para cancelar)_"
                )
                await enviar_mensaje_telegram(chat_id, pregunta)
                return
            
            success = sheets_client.update_transaction(parse_result.transaction)
            if success:
                respuesta = (
                    f"✅ *Registro Actualizado Exitosamente:*\n"
                    f"- *Monto:* ${parse_result.transaction.monto:,.0f}\n"
                    f"- *Categoría:* {parse_result.transaction.categoria}\n"
                    f"- *Tipo:* {parse_result.transaction.tipo.value}\n"
                    f"- *Fecha:* {parse_result.transaction.fecha}\n"
                    f"- *Metodo:* {parse_result.transaction.metodo.value}\n"
                    f"- *Concepto:* {parse_result.transaction.concepto}"
                )
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {"text": "✏️ Volver a Editar", "callback_data": f"edit:{parse_result.transaction.id_transaccion}"},
                            {"text": "🗑️ Borrar Definitivamente", "callback_data": f"delete:{parse_result.transaction.id_transaccion}"}
                        ]
                    ]
                }
                await enviar_mensaje_telegram(chat_id, respuesta, reply_markup=reply_markup)
            else:
                await enviar_mensaje_telegram(chat_id, "❌ Error actualizando en Google Sheets.")
                
            # Limpiar estado
            session.state = UserState.IDLE
            session.edit_transaction_id = None
            
        except ValueError as ve:
            await enviar_mensaje_telegram(chat_id, f"⚠️ No pude entender la corrección:\n_{ve}_")
        except Exception as e:
            logger.error(f"Error en edición: {e}")
            await enviar_mensaje_telegram(chat_id, "❌ Error interno procesando tu edición.")
        return

    # --- FLUJO 3: MENSAJE NUEVO ---
    texto_limpio = text.strip().lower()
    
    # Comandos de Sistema
    if texto_limpio in ["/ayuda", "ayuda", "help"]:
        msg = (
            "🤖 *Comandos Disponibles:*\n\n"
            "Solo escríbeme lo que gastaste (ej: _'15000 en uber'_).\n\n"
            "O usa estos comandos:\n"
            "📊 `/resumen` : Ver tus gastos acumulados del mes por categoría.\n"
            "🕰️ `/ultimas` : Ver tus últimos 5 registros y modificarlos.\n"
            "❓ `/ayuda` : Ver este mensaje."
        )
        await enviar_mensaje_telegram(chat_id, msg)
        return

    if texto_limpio.startswith("/resumen") or texto_limpio.startswith("resumen"):
        # Detectar si quiere el mes anterior
        month_offset = 0
        if "anterior" in texto_limpio or "pasado" in texto_limpio:
            month_offset = -1
            
        await enviar_mensaje_telegram(chat_id, "⏳ Consultando tu planilla...")
        resumen, t_month, t_year = sheets_client.get_month_summary(month_offset=month_offset)
        
        if not resumen:
            await enviar_mensaje_telegram(chat_id, f"ℹ️ No hay gastos registrados para {t_month:02d}/{t_year} o hubo un error.")
            return
            
        import io
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        
        msg_lineas = [f"📊 *Resumen de Gastos - {t_month:02d}/{t_year}*\n"]
        total = 0
        
        categorias = []
        montos = []
        
        # Diccionario de colores fijos para mantener consistencia visual
        CATEGORY_COLORS = {
            "Alimentación": "#ff9999", "Deportes": "#66b3ff", "Hogar": "#99ff99", 
            "Inversiones": "#ffcc99", "Mesada": "#c2c2f0", "Salidas": "#ffb3e6", 
            "Salud": "#c4e17f", "Telefonía": "#76D7C4", "Transporte": "#F7DC6F", 
            "Remuneraciones": "#82E0AA", "Otros Gastos": "#BFC9CA", "Otros Ingresos": "#F8C471"
        }
        default_colors = plt.cm.tab20.colors
        colores_usados = []
        
        for cat, datos in sorted(resumen.items(), key=lambda x: x[1]["total"], reverse=True):
            msg_lineas.append(f"- *{cat}:* ${datos['total']:,.0f} ({datos['count']} txs)")
            total += datos['total']
            categorias.append(cat)
            montos.append(datos['total'])
            # Asignar color fijo o fallback
            color = CATEGORY_COLORS.get(cat, default_colors[len(colores_usados) % len(default_colors)])
            colores_usados.append(color)
            
        msg_lineas.append(f"\n💰 *Total Gastado:* ${total:,.0f}")
        
        # Generar gráfico
        try:
            fig, ax = plt.subplots(figsize=(8, 6), subplot_kw=dict(aspect="equal"))
            
            wedges, texts, autotexts = ax.pie(
                montos, autopct='%1.1f%%', textprops=dict(color="w", weight="bold"), 
                colors=colores_usados, startangle=140
            )
            
            ax.legend(wedges, categorias, title="Categorías", loc="center left", bbox_to_anchor=(1, 0, 0.5, 1))
            ax.set_title(f"Gastos - Mes {t_month:02d}/{t_year}", pad=20, fontsize=14, fontweight="bold")
            
            buf = io.BytesIO()
            plt.savefig(buf, format='png', bbox_inches="tight")
            buf.seek(0)
            plt.close(fig)
            
            await enviar_foto_telegram(chat_id, buf.getvalue(), caption="\n".join(msg_lineas))
        except Exception as e:
            logger.error(f"Error generando gráfico: {e}")
            # Fallback a texto
            await enviar_mensaje_telegram(chat_id, "\n".join(msg_lineas))
            
        return

    if texto_limpio in ["/ultimas", "últimas", "ultimas"]:
        await enviar_mensaje_telegram(chat_id, "⏳ Obteniendo transacciones recientes...")
        ultimas = sheets_client.get_last_transactions(limit=5)
        if not ultimas:
            await enviar_mensaje_telegram(chat_id, "ℹ️ No hay transacciones recientes registradas.")
            return
            
        await enviar_mensaje_telegram(chat_id, "🕰️ *Tus últimos 5 movimientos:*")
        
        for tx in ultimas:
            # Emoji según tipo
            emoji = "🔴" if str(tx.get("tipo", "")).lower() == "gasto" else "🟢"
            
            detalle = (
                f"{emoji} *{tx['concepto']}* (${tx['monto']:,.0f})\n"
                f"📅 {tx['fecha']} | 📁 {tx['categoria']} | 💳 {tx['metodo']}"
            )
            
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "✏️ Editar", "callback_data": f"edit:{tx['id']}"},
                        {"text": "🗑️ Borrar", "callback_data": f"delete:{tx['id']}"}
                    ]
                ]
            }
            await enviar_mensaje_telegram(chat_id, detalle, reply_markup=reply_markup)
        return

    saludos = ["hola", "buenas", "buenos dias", "buenos días", "buenas tardes", "buenas noches", "/start", "start", "hello"]
    if texto_limpio in saludos:
        mensaje_bienvenida = (
            "¡Hola! 👋 Soy tu Bot Financiero.\n"
            "Dime qué gastaste o ingresaste y yo lo anotaré en tu planilla.\n"
            "Ejemplos:\n"
            "• _'Gasté 15000 en uber'_\n"
            "• _'Me pagaron 50 lucas que me debían'_\n"
            "• _'? cuánto he gastado en transporte este mes'_"
        )
        await enviar_mensaje_telegram(chat_id, mensaje_bienvenida)
        return

    # Comandos Analíticos (NLQ)
    if texto_limpio.startswith("/consulta ") or texto_limpio.startswith("? ") or texto_limpio.startswith("?"):
        pregunta = text.replace("/consulta", "").lstrip("? ").strip()
        if not pregunta:
            await enviar_mensaje_telegram(chat_id, "ℹ️ Por favor escribe tu pregunta después de `/consulta` o `?`.\nEjemplo: `? cuánto gasté en comida la semana pasada`")
            return
            
        await enviar_mensaje_telegram(chat_id, "⏳ Analizando tu historial (esto tomará unos segundos)...")
        ultimas_transacciones = sheets_client.get_last_transactions(limit=1000)
        
        import json
        datos_json = json.dumps(ultimas_transacciones, ensure_ascii=False)
        
        try:
            from src.parser import responder_consulta_natural
            respuesta_ai = responder_consulta_natural(pregunta, datos_json)
            await enviar_mensaje_telegram(chat_id, f"💡 *Analista Financiero:*\n\n{respuesta_ai}")
        except Exception as e:
            await enviar_mensaje_telegram(chat_id, f"❌ Hubo un error procesando tu consulta: {e}")
        return

    # Registro Múltiple (/multi)
    if texto_limpio.startswith("/multi ") or texto_limpio == "/multi":
        texto_multi = text.replace("/multi", "", 1).strip()
        if not texto_multi:
            await enviar_mensaje_telegram(chat_id, "ℹ️ Escribe tus gastos después del comando.\nEjemplo: `/multi 15k uber, 50k super`")
            return
            
        await enviar_mensaje_telegram(chat_id, "⏳ Procesando múltiples registros...")
        try:
            from src.parser import parse_multi_transaction_message
            transacciones = parse_multi_transaction_message(texto_multi, base_message_id=f"TG-{message_id}")
            
            if not transacciones:
                await enviar_mensaje_telegram(chat_id, "❌ No encontré gastos válidos en tu mensaje.")
                return
                
            success = sheets_client.append_multiple_transactions(transacciones)
            if success:
                resumen_lineas = [f"✅ *{len(transacciones)} Registros guardados:*"]
                for tx in transacciones:
                    emoji = "🔴" if str(tx.tipo.value).lower() == "gasto" else "🟢"
                    resumen_lineas.append(f"- {emoji} {tx.categoria}: ${tx.monto:,.0f} ({tx.concepto})")
                resumen_lineas.append("\n_(Usa `/ultimas` si necesitas editar alguno)_")
                
                await enviar_mensaje_telegram(chat_id, "\n".join(resumen_lineas))
            else:
                await enviar_mensaje_telegram(chat_id, "❌ Error guardando en Google Sheets.")
        except ValueError as e:
            await enviar_mensaje_telegram(chat_id, f"⚠️ Error en registro múltiple:\n_{str(e)}_")
        return

    try:
        parse_result = parse_transaction_message(text, message_id=f"TG-{message_id}")
        
        if parse_result.es_ambiguo and parse_result.opciones_categoria:
            session.state = UserState.AWAITING_CONFIRMATION
            session.pending_transaction = parse_result.transaction
            session.options = parse_result.opciones_categoria
            
            opciones_list = "\n".join([f"{i+1}. {op}" for i, op in enumerate(session.options)])
            pregunta = (
                f"🤔 Parece que gastaste ${parse_result.transaction.monto:,.0f} en '{parse_result.transaction.concepto}'.\n"
                f"No estoy seguro de la categoría. ¿Cuál es?\n"
                f"{opciones_list}\n"
                f"_(Responde con el número, el nombre de la categoría, o cualquier otra cosa para cancelar)_"
            )
            await enviar_mensaje_telegram(chat_id, pregunta)
            
        else:
            # Procesamiento directo
            success = sheets_client.append_transaction(parse_result.transaction)
            if success:
                respuesta = (
                    f"✅ Registrado exitosamente:\n"
                    f"- *Monto:* ${parse_result.transaction.monto:,.0f}\n"
                    f"- *Categoría:* {parse_result.transaction.categoria}\n"
                    f"- *Tipo:* {parse_result.transaction.tipo.value}\n"
                    f"- *Fecha:* {parse_result.transaction.fecha}\n"
                    f"- *Metodo:* {parse_result.transaction.metodo.value}\n"
                    f"- *Concepto:* {parse_result.transaction.concepto}"
                )
                
                # Adjuntamos botones para editar o deshacer
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {"text": "✏️ Editar", "callback_data": f"edit:{parse_result.transaction.id_transaccion}"},
                            {"text": "🗑️ Deshacer", "callback_data": f"delete:{parse_result.transaction.id_transaccion}"}
                        ]
                    ]
                }
                await enviar_mensaje_telegram(chat_id, respuesta, reply_markup=reply_markup)
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
    """Endpoint para recibir actualizaciones de Telegram."""
    if not sheets_client:
        raise HTTPException(status_code=500, detail="El cliente de Google Sheets no está configurado.")

    update = await request.json()
    
    # Manejo de Callback Query (Botones)
    callback_query = update.get("callback_query")
    if callback_query:
        callback_data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        chat_id = str(message.get("chat", {}).get("id"))
        
        if chat_id != ALLOWED_USER_ID:
            return {"status": "forbidden"}
            
        background_tasks.add_task(process_telegram_callback, chat_id, callback_data)
        
        # Debemos responder al webhook de telegram
        callback_id = callback_query.get("id")
        async with httpx.AsyncClient() as client:
            await client.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery?callback_query_id={callback_id}")
            
        return {"status": "ok"}
    
    # Manejo de Texto
    message = update.get("message")
    if not message:
        return {"status": "ignored", "reason": "Not a message or callback update"}
        
    chat_id = str(message.get("chat", {}).get("id"))
    text = message.get("text", "")
    message_id = str(message.get("message_id"))

    if chat_id != ALLOWED_USER_ID:
        return {"status": "forbidden"}

    if not text:
        return {"status": "ignored", "reason": "Empty text"}

    background_tasks.add_task(process_telegram_update, chat_id, text, message_id)
    
    return {"status": "ok"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}
