import os
import logging
import httpx
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from dotenv import load_dotenv

from src.parser import parse_transaction_message
from src.sheets_client import GoogleSheetsClient
from src.state import get_user_session, UserState
from src.models import format_currency, Transaction, MetodoPago

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

async def finalizar_guardado_transaccion(chat_id: str, session, tx: Transaction, es_edicion: bool = False):
    """Guarda o actualiza la transacción confirmada, calcula alertas y envía feedback al usuario."""
    if es_edicion and session.edit_transaction_id:
        tx.id_transaccion = session.edit_transaction_id
        success = sheets_client.update_transaction(tx)
        msg_exito = (
            f"✅ *Registro Actualizado Exitosamente:*\n"
            f"- *Monto:* {format_currency(tx.monto)}\n"
            f"- *Categoría:* {tx.categoria}\n"
            f"- *Tipo:* {tx.tipo.value}\n"
            f"- *Fecha:* {tx.fecha}\n"
            f"- *Método:* {tx.metodo.value}\n"
            f"- *Concepto:* {tx.concepto}"
        )
    else:
        success = sheets_client.append_transaction(tx)
        
        # INYECCIÓN DE PRESUPUESTO (Solo para gastos nuevos y estrictos)
        es_anomalo = False
        estado_presupuesto = None
        if str(tx.tipo.value).lower() == "gasto" and sheets_client:
            resumen_mes, _, _ = sheets_client.get_month_summary(0)
            acumulado = resumen_mes.get(tx.categoria, {}).get("total", 0)
            
            promedio = sheets_client.get_category_monthly_average(tx.categoria)
            if promedio > 0 and acumulado > promedio * 1.5:
                es_anomalo = True
                
            is_strict = tx.categoria not in ["Ahorro", "Inversiones", "Salud", "Cuentas Básicas", "Educación", "Remuneraciones", "Otros Ingresos"]
            if is_strict:
                cat_config = sheets_client.load_categories_from_config().get(tx.categoria, {})
                presupuesto = cat_config.get("presupuesto") if isinstance(cat_config, dict) else None
                
                if presupuesto and presupuesto > 0:
                    if acumulado > presupuesto:
                        estado_presupuesto = f"Lleva gastado {format_currency(acumulado)} en el mes, y su límite es {format_currency(presupuesto)}. ¡Se excedió!"
                    elif acumulado >= presupuesto * 0.8:
                        estado_presupuesto = f"Lleva gastado {format_currency(acumulado)} en el mes, y su límite es {format_currency(presupuesto)}. ¡Está peligrosamente cerca!"
        
        from src.parser import generar_comentario_ironico
        chiste = generar_comentario_ironico(
            tx.monto, 
            tx.concepto, 
            tx.categoria,
            estado_presupuesto=estado_presupuesto,
            es_anomalo=es_anomalo
        )
        
        msg_exito = (
            f"✅ Registrado exitosamente:\n"
            f"- *Monto:* {format_currency(tx.monto)}\n"
            f"- *Categoría:* {tx.categoria}\n"
            f"- *Método:* {tx.metodo.value}\n"
            f"- *Tipo:* {tx.tipo.value}\n"
            f"- *Fecha:* {tx.fecha}"
        )
        if chiste:
            msg_exito += f"\n\n🤖 _{chiste}_"
            
    if success:
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✏️ Editar", "callback_data": f"edit:{tx.id_transaccion}"},
                    {"text": "🗑️ Deshacer", "callback_data": f"delete:{tx.id_transaccion}"}
                ]
            ]
        }
        await enviar_mensaje_telegram(chat_id, msg_exito, reply_markup=reply_markup)
    else:
        await enviar_mensaje_telegram(chat_id, "❌ Error guardando en Google Sheets.")

    # Limpiar estado
    session.state = UserState.IDLE
    session.pending_transaction = None
    session.options = []
    session.edit_transaction_id = None

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

    elif callback_data.startswith("cat:"):
        cat_elegida = callback_data.split(":", 1)[1]
        if session.state == UserState.AWAITING_CONFIRMATION and session.pending_transaction:
            session.pending_transaction.categoria = cat_elegida
            es_edicion = bool(session.edit_transaction_id)
            await finalizar_guardado_transaccion(chat_id, session, session.pending_transaction, es_edicion=es_edicion)

    elif callback_data.startswith("metodo:"):
        metodo_str = callback_data.split(":", 1)[1]
        if session.state == UserState.AWAITING_METHOD_CONFIRMATION and session.pending_transaction:
            try:
                session.pending_transaction.metodo = MetodoPago(metodo_str)
            except ValueError:
                session.pending_transaction.metodo = MetodoPago.PLANILLA if metodo_str.lower() == "planilla" else MetodoPago.DEBITO
            es_edicion = bool(session.edit_transaction_id)
            await finalizar_guardado_transaccion(chat_id, session, session.pending_transaction, es_edicion=es_edicion)

async def process_telegram_update(chat_id: str, text: str, message_id: str):
    """
    Lógica conversacional que se ejecuta en Background para no bloquear a Telegram.
    """
    session = get_user_session(int(chat_id))

    texto_limpio = text.strip().lower()
    
    comandos_sistema = ["/ayuda", "ayuda", "help", "/cancelar", "cancelar", "/buscar", "/ultimas", "últimas", "ultimas", "/resumen", "resumen", "/multi", "/start", "start", "hola", "buenas"]
    is_command = any(texto_limpio.startswith(cmd) for cmd in comandos_sistema) or texto_limpio.startswith("?")
    
    if is_command or texto_limpio == "cancelar":
        if session.state != UserState.IDLE:
            session.state = UserState.IDLE
            session.pending_transaction = None
            session.options = []
            session.edit_transaction_id = None
            if texto_limpio in ["/cancelar", "cancelar"]:
                await enviar_mensaje_telegram(chat_id, "🚫 Operación cancelada.")
                return
        elif texto_limpio in ["/cancelar", "cancelar"]:
            await enviar_mensaje_telegram(chat_id, "ℹ️ No había ninguna operación pendiente.")
            return

    # --- FLUJO 1A: ESPERANDO CONFIRMACIÓN DE MÉTODO (PLANILLA VS DÉBITO) ---
    if session.state == UserState.AWAITING_METHOD_CONFIRMATION:
        metodo_elegido = None
        texto_resp = texto_limpio.strip()
        
        if "planilla" in texto_resp or texto_resp == "1":
            metodo_elegido = MetodoPago.PLANILLA
        elif "debito" in texto_resp or "débito" in texto_resp or texto_resp == "2":
            metodo_elegido = MetodoPago.DEBITO
        else:
            for op in session.options:
                if op.lower() in texto_resp:
                    metodo_elegido = MetodoPago(op)
                    break
                    
        if metodo_elegido and session.pending_transaction:
            session.pending_transaction.metodo = metodo_elegido
            es_edicion = bool(session.edit_transaction_id)
            await finalizar_guardado_transaccion(chat_id, session, session.pending_transaction, es_edicion=es_edicion)
            return
        else:
            await enviar_mensaje_telegram(chat_id, "⚠️ Descartando confirmación de método pendiente para procesar tu nuevo mensaje.")
            session.state = UserState.IDLE
            session.pending_transaction = None
            session.options = []
            session.edit_transaction_id = None

    # --- FLUJO 1B: ESPERANDO CONFIRMACIÓN DE CATEGORÍA ---
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

        if categoria_elegida and session.pending_transaction:
            session.pending_transaction.categoria = categoria_elegida
            es_edicion = bool(session.edit_transaction_id)
            await finalizar_guardado_transaccion(chat_id, session, session.pending_transaction, es_edicion=es_edicion)
            return
        else:
            await enviar_mensaje_telegram(chat_id, "⚠️ Descartando gasto ambiguo para procesar tu nuevo mensaje.")
            session.state = UserState.IDLE
            session.pending_transaction = None
            session.options = []
            session.edit_transaction_id = None

    # --- FLUJO 2: EDITANDO UNA TRANSACCIÓN (TEXTO LIBRE) ---
    if session.state == UserState.AWAITING_EDIT:
        try:
            # Parseamos usando el ID antiguo para sobrescribir
            categorias_list = list(sheets_client.load_categories_from_config().keys()) if sheets_client else []
            parse_result = parse_transaction_message(text, message_id=session.edit_transaction_id, categorias_disponibles=categorias_list)
            
            if parse_result.es_ambiguo and parse_result.opciones_categoria:
                session.state = UserState.AWAITING_CONFIRMATION
                session.pending_transaction = parse_result.transaction
                session.options = parse_result.opciones_categoria
                
                opciones_list = "\n".join([f"{i+1}. {op}" for i, op in enumerate(session.options)])
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": f"{i+1}. {op}", "callback_data": f"cat:{op}"} for i, op in enumerate(session.options)]
                    ]
                }
                pregunta = (
                    f"🤔 Parece que la corrección es por {format_currency(parse_result.transaction.monto)} en '{parse_result.transaction.concepto}'.\n"
                    f"No estoy seguro de la categoría. ¿Cuál es?\n"
                    f"{opciones_list}\n"
                    f"_(Toca un botón o responde con el número)_"
                )
                await enviar_mensaje_telegram(chat_id, pregunta, reply_markup=reply_markup)
                return

            elif parse_result.es_ambiguo_metodo and parse_result.opciones_metodo:
                session.state = UserState.AWAITING_METHOD_CONFIRMATION
                session.pending_transaction = parse_result.transaction
                session.options = parse_result.opciones_metodo
                
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {"text": "🏢 Planilla", "callback_data": "metodo:Planilla"},
                            {"text": "💳 Débito", "callback_data": "metodo:Débito"}
                        ]
                    ]
                }
                pregunta = (
                    f"🤔 En la corrección por {format_currency(parse_result.transaction.monto)} en '{parse_result.transaction.concepto}' ({parse_result.transaction.categoria}),\n"
                    f"¿cómo fue pagado o descontado?\n\n"
                    f"1. 🏢 *Planilla* (descuento en liquidación de sueldo)\n"
                    f"2. 💳 *Débito* (o tarjeta bancaria)\n\n"
                    f"_(Toca un botón o responde 1 o 2)_"
                )
                await enviar_mensaje_telegram(chat_id, pregunta, reply_markup=reply_markup)
                return
            
            await finalizar_guardado_transaccion(chat_id, session, parse_result.transaction, es_edicion=True)
            
        except ValueError as ve:
            await enviar_mensaje_telegram(chat_id, f"⚠️ No pude entender la corrección:\n_{ve}_\n\n_(Si quieres salir del modo edición, escribe /cancelar)_")
        except Exception as e:
            logger.error(f"Error en edición: {e}")
            await enviar_mensaje_telegram(chat_id, "❌ Error interno procesando tu edición.")
        return

    # --- FLUJO 3: MENSAJE NUEVO ---
    if texto_limpio in ["/ayuda", "ayuda", "help"]:
        msg = (
            "🤖 *Comandos Disponibles:*\n\n"
            "Solo escríbeme lo que gastaste o ingresaste de forma natural:\n"
            "• _'15000 en uber'_\n"
            "• _'3500 almuerzo casino pega'_ (automático por *Planilla*, no duplica descuentos de tu sueldo líquido)\n"
            "• _'20000 taller deportivo por planilla'_\n"
            "• _'Me pagaron 50 lucas que me debían'_\n\n"
            "O usa estos comandos avanzados:\n"
            "📊 `/resumen` : Ver tus gastos del mes (con desglose en cuenta/tarjetas vs. planilla).\n"
            "📈 `/tendencias` : Compara tus gastos de este mes (hasta hoy) con el mes pasado.\n"
            "⏪ `/resumen anterior` : Ver tus gastos del mes pasado.\n"
            "🕰️ `/ultimas` : Ver tus últimos 5 registros (te permite Editarlos o Borrarlos).\n"
            "📦 `/multi [gastos]` : Registra varios gastos de una sola vez separados por comas (ej: _/multi 15k uber, 50k super_).\n"
            "🔎 `/buscar [texto]` : Busca registros por concepto o comentario (ej: _/buscar casino_).\n"
            "💡 `? [pregunta]` : Hazme cualquier consulta analítica sobre tus datos (ej: _? cuánto gasté en casino este mes_ o _? desglose por método_).\n"
            "❓ `/ayuda` : Ver este mensaje.\n\n"
            "💡 *Tip:* Puedes entrar a tu planilla de Sheets y agregar montos en la columna 'Presupuesto' de la pestaña 'Config' para que el bot controle tus límites mensuales."
        )
        await enviar_mensaje_telegram(chat_id, msg)
        return
        
    if texto_limpio.startswith("/tendencias") or texto_limpio.startswith("tendencias"):
        await enviar_mensaje_telegram(chat_id, "⏳ Analizando tus tendencias de gasto...")
        from src.models import get_local_date
        hoy = get_local_date()
        day = hoy.day
        
        # Obtenemos los resúmenes hasta el día actual
        resumen_actual, m_act, y_act = sheets_client.get_month_summary(0, max_day=day)
        resumen_pasado, m_pas, y_pas = sheets_client.get_month_summary(-1, max_day=day)
        
        categorias_ingreso_nativas = ["Remuneraciones", "Otros Ingresos", "Inversiones"]
        
        gastos_actual = sum(datos['total'] for cat, datos in resumen_actual.items() if cat not in categorias_ingreso_nativas)
        gastos_pasado = sum(datos['total'] for cat, datos in resumen_pasado.items() if cat not in categorias_ingreso_nativas)
        
        if gastos_pasado == 0:
            await enviar_mensaje_telegram(chat_id, "ℹ️ No tienes suficientes gastos registrados el mes pasado para hacer una comparación.")
            return
            
        variacion_total = ((gastos_actual - gastos_pasado) / gastos_pasado) * 100
        emoji_total = "🔴 Subió" if variacion_total > 0 else "🟢 Bajó"
        
        msg = f"📈 *Tendencias de Gasto (hasta el día {day})*\n\n"
        msg += f"🗓️ {m_act:02d}/{y_act}: {format_currency(gastos_actual)}\n"
        msg += f"🗓️ {m_pas:02d}/{y_pas}: {format_currency(gastos_pasado)}\n"
        msg += f"📊 Variación: {emoji_total} un {abs(variacion_total):.1f}%\n\n"
        
        # Categorías que más subieron
        variaciones = []
        for cat, datos in resumen_actual.items():
            if cat in categorias_ingreso_nativas:
                continue
                
            monto_actual = datos['total']
            monto_pasado = resumen_pasado.get(cat, {}).get("total", 0)
            diff = monto_actual - monto_pasado
            if diff > 0:
                variaciones.append((cat, diff, monto_actual, monto_pasado))
                
        if variaciones:
            msg += "*🔥 Categorías que más aumentaron:*\n"
            variaciones.sort(key=lambda x: x[1], reverse=True)
            for cat, diff, m_act_cat, m_pas_cat in variaciones[:3]:
                pct = (diff / m_pas_cat * 100) if m_pas_cat > 0 else 100
                msg += f"• *{cat}:* +{format_currency(diff)} (⬆️ {pct:.0f}%)\n"
        else:
            msg += "🏆 ¡Excelente! Ninguna categoría ha subido respecto al mes pasado.\n"
            
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
        
        msg_lineas = [f"📊 *Resumen Mensual - {t_month:02d}/{t_year}*\n"]
        total_gastos = 0
        total_ingresos = 0
        total_planilla = 0
        
        categorias = []
        montos = []
        
        # Diccionario de configuración desde Google Sheets
        CATEGORY_CONFIG = sheets_client.load_categories_from_config() if sheets_client else {}
        default_colors = plt.cm.tab20.colors
        colores_usados = []
        
        categorias_ingreso_nativas = ["Remuneraciones", "Otros Ingresos", "Inversiones"]
        
        for cat, datos in sorted(resumen.items(), key=lambda x: x[1]["total"], reverse=True):
            cat_config = CATEGORY_CONFIG.get(cat, {})
            presupuesto = cat_config.get("presupuesto") if isinstance(cat_config, dict) else None
            
            is_ingreso = cat in categorias_ingreso_nativas
            
            if is_ingreso:
                total_ingresos += datos['total']
                msg_lineas.append(f"🟢 *{cat}:* {format_currency(datos['total'])} ({datos['count']} txs)")
            else:
                total_gastos += datos['total']
                total_planilla += datos.get('planilla', 0)
                categorias.append(cat)
                montos.append(datos['total'])
                
                # Asignar color fijo o fallback
                color_hex = cat_config.get("color") if isinstance(cat_config, dict) else None
                color = color_hex if color_hex else default_colors[len(colores_usados) % len(default_colors)]
                colores_usados.append(color)
                
                if presupuesto and presupuesto > 0:
                    pct = (datos['total'] / presupuesto) * 100
                    is_strict = cat not in ["Ahorro", "Inversiones", "Salud", "Cuentas Básicas", "Educación", "Remuneraciones", "Otros Ingresos", "Hogar"]
                    
                    if pct > 100:
                        alert = "🔴 EXCEDIDO" if is_strict else "🔵 Completado"
                        msg_lineas.append(f"- *{cat}:* {format_currency(datos['total'])} / {format_currency(presupuesto)} ({pct:.0f}% {alert})")
                    elif pct < 0:
                        msg_lineas.append(f"- *{cat}:* {format_currency(datos['total'])} / {format_currency(presupuesto)} (🟢 Aporte neto a favor)")
                    else:
                        msg_lineas.append(f"- *{cat}:* {format_currency(datos['total'])} / {format_currency(presupuesto)} ({pct:.0f}% 🟢)")
                else:
                    if datos['total'] < 0:
                        msg_lineas.append(f"- *{cat}:* {format_currency(datos['total'])} ({datos['count']} txs | 🟢 aporte neto a favor)")
                    else:
                        msg_lineas.append(f"- *{cat}:* {format_currency(datos['total'])} ({datos['count']} txs)")
            
        msg_lineas.append(f"\n💰 *Total Ingresos:* {format_currency(total_ingresos)}")
        if total_planilla > 0:
            gastos_cuenta = total_gastos - total_planilla
            msg_lineas.append(f"💸 *Total Gastos:* {format_currency(total_gastos)}")
            msg_lineas.append(f"  ├─ 💳 *En cuenta/tarjetas:* {format_currency(gastos_cuenta)}")
            msg_lineas.append(f"  └─ 🏢 *Por planilla:* {format_currency(total_planilla)}")
            balance = total_ingresos - gastos_cuenta
        else:
            msg_lineas.append(f"💸 *Total Gastos:* {format_currency(total_gastos)}")
            balance = total_ingresos - total_gastos
            
        if total_ingresos > 0:
            ahorro_pct = (balance / total_ingresos) * 100
            msg_lineas.append(f"⚖️ *Balance Neto en Cuenta:* {format_currency(balance)} ({ahorro_pct:.1f}% ahorrado)")
        else:
            msg_lineas.append(f"⚖️ *Balance Neto en Cuenta:* {format_currency(balance)}")
        
        # Generar gráfico solo para categorías con gasto positivo (ax.pie no admite valores negativos ni cero)
        datos_grafico = [
            (cat, m, col)
            for cat, m, col in zip(categorias, montos, colores_usados)
            if m > 0
        ]
        
        try:
            if not datos_grafico:
                await enviar_mensaje_telegram(chat_id, "\n".join(msg_lineas))
                return
                
            from src.models import get_local_date
            hora_gen = get_local_date().strftime("%d/%m/%Y")
            
            cat_graf = [d[0] for d in datos_grafico]
            montos_graf = [d[1] for d in datos_grafico]
            colores_graf = [d[2] for d in datos_grafico]
            
            fig, ax = plt.subplots(figsize=(8, 6), subplot_kw=dict(aspect="equal"))
            
            wedges, texts, autotexts = ax.pie(
                montos_graf, autopct='%1.1f%%', textprops=dict(color="w", weight="bold"), 
                colors=colores_graf, startangle=140
            )
            
            ax.legend(wedges, cat_graf, title="Categorías", loc="center left", bbox_to_anchor=(1, 0, 0.5, 1))
            fig.suptitle(f"Gastos - Mes {t_month:02d}/{t_year}", fontsize=14, fontweight="bold", y=0.98)
            ax.set_title(f"Generado el: {hora_gen}", fontsize=10, color="gray", pad=15)
            
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

    if texto_limpio.startswith("/ultimas") or texto_limpio.startswith("últimas") or texto_limpio.startswith("ultimas"):
        ordenar_por_fecha = "fecha" in texto_limpio
        await enviar_mensaje_telegram(chat_id, "⏳ Obteniendo transacciones recientes...")
        ultimas = sheets_client.get_last_transactions(limit=50 if ordenar_por_fecha else 5)
        if not ultimas:
            await enviar_mensaje_telegram(chat_id, "ℹ️ No hay transacciones recientes registradas.")
            return
            
        if ordenar_por_fecha:
            try:
                from datetime import datetime
                # Ordenar por fecha descendente, y luego por ID descendente
                ultimas.sort(key=lambda x: (datetime.strptime(x['fecha'], "%d-%m-%Y"), int(x['id'])), reverse=True)
            except Exception as e:
                logger.error(f"Error ordenando por fecha: {e}")
            # Truncar a 5
            ultimas = ultimas[:5]
            
        titulo = "🕰️ *Tus 5 movimientos más recientes (por fecha):*" if ordenar_por_fecha else "🕰️ *Tus últimos 5 movimientos registrados:*"
        await enviar_mensaje_telegram(chat_id, titulo)
        
        for tx in ultimas:
            # Emoji según tipo
            emoji = "🔴" if str(tx.get("tipo", "")).lower() == "gasto" else "🟢"
            
            comentario_str = f"\n📝 _{tx.get('comentarios', '')}_" if tx.get('comentarios') else ""
            
            detalle = (
                f"{emoji} *{tx['concepto']}* ({format_currency(tx['monto'])})\n"
                f"📅 {tx['fecha']} | 📁 {tx['categoria']} | 💳 {tx['metodo']}{comentario_str}"
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

    # Búsqueda (/buscar)
    if texto_limpio.startswith("/buscar ") or texto_limpio == "/buscar":
        texto_busqueda = text.replace("/buscar", "", 1).strip()
        if not texto_busqueda:
            await enviar_mensaje_telegram(chat_id, "ℹ️ Escribe lo que quieres buscar después del comando.\nEjemplo: `/buscar uber`")
            return
            
        await enviar_mensaje_telegram(chat_id, f"⏳ Buscando '{texto_busqueda}'...")
        resultados = sheets_client.search_transactions(query=texto_busqueda, limit=5)
        
        if not resultados:
            await enviar_mensaje_telegram(chat_id, "ℹ️ No encontré registros que coincidan con tu búsqueda.")
            return
            
        await enviar_mensaje_telegram(chat_id, f"🔎 *Resultados de búsqueda ({len(resultados)}):*")
        
        for tx in resultados:
            emoji = "🔴" if str(tx.get("tipo", "")).lower() == "gasto" else "🟢"
            comentario_str = f"\n📝 _{tx.get('comentarios', '')}_" if tx.get('comentarios') else ""
            
            detalle = (
                f"{emoji} *{tx['concepto']}* ({format_currency(tx['monto'])})\n"
                f"📅 {tx['fecha']} | 📁 {tx['categoria']} | 💳 {tx['metodo']}{comentario_str}"
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
            "Dime qué gastaste o ingresaste y yo lo anotaré en tu planilla.\n\n"
            "💡 *Ejemplos:*\n"
            "• _'Gasté 15000 en uber'_\n"
            "• _'3500 almuerzo menu casino pega'_ (automático por planilla 🏢)\n"
            "• _'Me pagaron 50 lucas que me debían'_\n"
            "• _'? cuánto he gastado en casino este mes'_\n\n"
            "⚙️ *Comandos:*\n"
            "• `/resumen` - Resumen del mes y gráfico con desglose cuenta/planilla.\n"
            "• `/buscar <texto>` - Busca registros por concepto o comentario.\n"
            "• `/ultimas` - Muestra los últimos 5 registros con opciones de edición.\n"
            "• `/multi` - Registra varios gastos a la vez.\n"
            "• `/ayuda` - Ver todos los comandos y detalles."
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
        
        try:
            from src.parser import responder_consulta_natural
            respuesta_ai = responder_consulta_natural(pregunta, ultimas_transacciones)
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
            categorias_list = list(sheets_client.load_categories_from_config().keys()) if sheets_client else []
            transacciones = parse_multi_transaction_message(texto_multi, base_message_id=f"TG-{message_id}", categorias_disponibles=categorias_list)
            
            if not transacciones:
                await enviar_mensaje_telegram(chat_id, "❌ No encontré gastos válidos en tu mensaje.")
                return
                
            success = sheets_client.append_multiple_transactions(transacciones)
            if success:
                resumen_lineas = [f"✅ *{len(transacciones)} Registros guardados:*"]
                for tx in transacciones:
                    emoji = "🔴" if str(tx.tipo.value).lower() == "gasto" else "🟢"
                    resumen_lineas.append(f"- {emoji} {tx.categoria}: {format_currency(tx.monto)} ({tx.concepto})")
                resumen_lineas.append("\n_(Usa `/ultimas` si necesitas editar alguno)_")
                
                await enviar_mensaje_telegram(chat_id, "\n".join(resumen_lineas))
            else:
                await enviar_mensaje_telegram(chat_id, "❌ Error guardando en Google Sheets.")
        except ValueError as e:
            await enviar_mensaje_telegram(chat_id, f"⚠️ Error en registro múltiple:\n_{str(e)}_")
        return

    try:
        categorias_list = list(sheets_client.load_categories_from_config().keys()) if sheets_client else []
        parse_result = parse_transaction_message(text, message_id=f"TG-{message_id}", categorias_disponibles=categorias_list)
        
        if parse_result.es_ambiguo and parse_result.opciones_categoria:
            session.state = UserState.AWAITING_CONFIRMATION
            session.pending_transaction = parse_result.transaction
            session.options = parse_result.opciones_categoria
            
            opciones_list = "\n".join([f"{i+1}. {op}" for i, op in enumerate(session.options)])
            reply_markup = {
                "inline_keyboard": [
                    [{"text": f"{i+1}. {op}", "callback_data": f"cat:{op}"} for i, op in enumerate(session.options)]
                ]
            }
            pregunta = (
                f"🤔 Parece que gastaste {format_currency(parse_result.transaction.monto)} en '{parse_result.transaction.concepto}'.\n"
                f"No estoy seguro de la categoría. ¿Cuál es?\n"
                f"{opciones_list}\n"
                f"_(Toca un botón o responde con el número)_"
            )
            await enviar_mensaje_telegram(chat_id, pregunta, reply_markup=reply_markup)
            
        elif parse_result.es_ambiguo_metodo and parse_result.opciones_metodo:
            session.state = UserState.AWAITING_METHOD_CONFIRMATION
            session.pending_transaction = parse_result.transaction
            session.options = parse_result.opciones_metodo
            
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "🏢 Planilla", "callback_data": "metodo:Planilla"},
                        {"text": "💳 Débito", "callback_data": "metodo:Débito"}
                    ]
                ]
            }
            pregunta = (
                f"🤔 Registré {format_currency(parse_result.transaction.monto)} en '{parse_result.transaction.concepto}' ({parse_result.transaction.categoria}).\n"
                f"¿Cómo lo pagaste o se descontará?\n\n"
                f"1. 🏢 *Planilla* (descuento en liquidación de sueldo)\n"
                f"2. 💳 *Débito* (o tarjeta bancaria)\n\n"
                f"_(Toca un botón o responde 1 o 2)_"
            )
            await enviar_mensaje_telegram(chat_id, pregunta, reply_markup=reply_markup)

        else:
            # Procesamiento directo
            await finalizar_guardado_transaccion(chat_id, session, parse_result.transaction, es_edicion=False)
                
    except ValueError as ve:
        logger.error(f"Error de validación/parseo: {ve}")
        
        # Extraer mensaje limpio si es un error de validación de Pydantic
        error_msg = str(ve)
        if hasattr(ve, 'errors') and callable(ve.errors):
            try:
                mensajes = []
                for err in ve.errors():
                    msg = err.get('msg', '')
                    if msg.startswith('Value error, '):
                        msg = msg.replace('Value error, ', '', 1)
                    mensajes.append(f"• {msg}")
                if mensajes:
                    error_msg = "\n".join(mensajes)
            except Exception:
                pass
                
        await enviar_mensaje_telegram(chat_id, f"⚠️ No pude procesar el mensaje por este motivo:\n_{error_msg}_")
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
