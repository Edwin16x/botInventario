import os
import json
import threading
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    ConversationHandler, ContextTypes, filters
)
from flask import Flask

# ==========================================
# 1. CONFIGURACIÓN DE IDs (PEGA LOS TUYOS AQUÍ)
# ==========================================
ID_SHEET = "1FVlZOft3MKbiJkPJVk5nbAD1sulVDXYbRFnANdDxVtw" # El ID de la URL de tu Sheet

app_flask = Flask(__name__)
SELECCIONAR_TABLA, RECOLECTAR_DATOS = range(2)

@app_flask.route('/')
def home():
    return "✅ Bot de Inventario Dinámico por ID en línea."

def run_web_server():
    # Render usa el puerto 10000 por defecto para Web Services
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port)

# ==========================================
# 2. CONEXIÓN A GOOGLE
# ==========================================
def conectar_sheets():
    try:
        google_json = os.environ.get("GOOGLE_CREDENTIALS")
        if not google_json:
            print("❌ ERROR: No se encontró la variable GOOGLE_CREDENTIALS")
            return None
            
        creds_dict = json.loads(google_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ])
        return gspread.authorize(creds)
    except Exception as e:
        print(f"❌ Error en autenticación: {e}")
        return None

cliente_gspread = conectar_sheets()

# ==========================================
# 3. MÁQUINA DE ESTADOS DINÁMICA
# ==========================================

async def iniciar_registro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Abrimos el libro usando el ID inmutable
        libro = cliente_gspread.open_by_key(ID_SHEET)
        hojas = libro.worksheets()
        
        # Generamos botones basados en las pestañas reales del Excel
        teclado = [[InlineKeyboardButton(f"📁 {h.title}", callback_data=h.title)] for h in hojas]
        reply_markup = InlineKeyboardMarkup(teclado)
        
        await update.message.reply_text("📦 *Sistema de Inventario*\nSelecciona la categoría:", 
                                      reply_markup=reply_markup, parse_mode='Markdown')
        return SELECCIONAR_TABLA
    except Exception as e:
        await update.message.reply_text(f"❌ Error al conectar con la Sheet ID: {e}")
        return ConversationHandler.END

async def seleccionar_tabla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    tabla_nombre = query.data
    context.user_data['tabla_nombre'] = tabla_nombre
    
    # Obtenemos la hoja y leemos la fila 1 (Encabezados)
    hoja = cliente_gspread.open_by_key(ID_SHEET).worksheet(tabla_nombre)
    encabezados = hoja.row_values(1)
    
    if not encabezados:
        await query.edit_message_text("❌ La tabla está vacía (sin encabezados en la Fila 1).")
        return ConversationHandler.END

    context.user_data['columnas'] = encabezados
    context.user_data['respuestas'] = []
    context.user_data['indice_pregunta'] = 0
    
    await query.edit_message_text(f"📝 Registro en: *{tabla_nombre}*\nIntroduce: *{encabezados[0]}*", parse_mode='Markdown')
    return RECOLECTAR_DATOS

async def recolectar_datos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    columnas = context.user_data['columnas']
    idx = context.user_data['indice_pregunta']
    
    # Guardamos la respuesta del usuario
    context.user_data['respuestas'].append(update.message.text)
    
    # Si faltan más columnas por preguntar
    if idx + 1 < len(columnas):
        context.user_data['indice_pregunta'] += 1
        siguiente_campo = columnas[idx + 1]
        await update.message.reply_text(f"Siguiente dato: *{siguiente_campo}*", parse_mode='Markdown')
        return RECOLECTAR_DATOS
    else:
        # Final de las preguntas, procedemos a guardar
        await update.message.reply_text("⏳ Procesando registro...")
        try:
            hoja = cliente_gspread.open_by_key(ID_SHEET).worksheet(context.user_data['tabla_nombre'])
            hoja.append_row(context.user_data['respuestas'])
            await update.message.reply_text(f"✅ ¡Éxito! Registro guardado en *{context.user_data['tabla_nombre']}*.", parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ Error al guardar fila: {e}")
        
        return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛑 Operación cancelada por el usuario.")
    return ConversationHandler.END

# ==========================================
# 4. INICIO DEL BOT
# ==========================================
def main():
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    if not TOKEN:
        print("❌ No hay TOKEN")
        return

    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('ingresar', iniciar_registro)],
        states={
            SELECCIONAR_TABLA: [CallbackQueryHandler(seleccionar_tabla)],
            RECOLECTAR_DATOS: [MessageHandler(filters.TEXT & ~filters.COMMAND, recolectar_datos)],
        },
        fallbacks=[CommandHandler('cancelar', cancelar)]
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Bot de Inventario. Usa /ingresar")))

    # Iniciar servidor web para Render
    threading.Thread(target=run_web_server, daemon=True).start()
    
    print("🤖 Bot iniciado y escuchando...")
    app.run_polling()

if __name__ == "__main__":
    main()