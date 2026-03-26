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
# 1. CONFIGURACIÓN Y SERVIDOR WEB
# ==========================================
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "✅ Inventario Online"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port)

# ==========================================
# 2. CONEXIÓN ROBUSTA A GOOGLE SHEETS
# ==========================================
# CAMBIA ESTO AL NOMBRE EXACTO DE TU ARCHIVO DE GOOGLE SHEETS
NOMBRE_ARCHIVO_SHEETS = "Inventario_Residencias" 

def conectar_sheets():
    try:
        google_json = os.environ.get("GOOGLE_CREDENTIALS")
        if not google_json:
            print("❌ ERROR: Falta variable GOOGLE_CREDENTIALS")
            return None
            
        creds_dict = json.loads(google_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ])
        return gspread.authorize(creds)
    except Exception as e:
        print(f"❌ Error conectando a Google: {e}")
        return None

# Instancia global del cliente
cliente_gspread = conectar_sheets()

# ==========================================
# 3. LÓGICA DEL BOT (MÁQUINA DE ESTADOS)
# ==========================================
CATEGORIA, PRODUCTO, CANTIDAD = range(3)

async def iniciar_ingreso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teclado = [
        [InlineKeyboardButton("🥾 Calzado", callback_data='Calzado')],
        [InlineKeyboardButton("🔧 Herramientas", callback_data='Herramientas')],
        [InlineKeyboardButton("🔌 Electrónica", callback_data='Electronica')]
    ]
    reply_markup = InlineKeyboardMarkup(teclado)
    await update.message.reply_text('¿En qué categoría vas a registrar?', reply_markup=reply_markup)
    return CATEGORIA

async def recibir_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['categoria'] = query.data
    await query.edit_message_text(f"Elegiste: *{query.data}*\nEscribe el nombre del producto:", parse_mode='Markdown')
    return PRODUCTO

async def recibir_producto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['producto'] = update.message.text
    await update.message.reply_text("Ingresa la cantidad (solo números):")
    return CANTIDAD

async def recibir_cantidad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cantidad_texto = update.message.text
    if not cantidad_texto.isdigit():
        await update.message.reply_text("⚠️ Por favor, ingresa solo números.")
        return CANTIDAD

    categoria = context.user_data['categoria']
    producto = context.user_data['producto']
    usuario = update.message.from_user.username or update.message.from_user.first_name
    
    await update.message.reply_text("⏳ Guardando en la base de datos...")

    try:
        # Forzar reconexión si el cliente falló
        global cliente_gspread
        if not cliente_gspread:
            cliente_gspread = conectar_sheets()

        # Abrir libro y hoja
        libro = cliente_gspread.open(NOMBRE_ARCHIVO_SHEETS)
        hoja = libro.worksheet(categoria)
        
        # Insertar datos: Producto, Cantidad, Usuario, Estado
        hoja.append_row([producto, int(cantidad_texto), usuario, "Registrado"])
        
        await update.message.reply_text(f"✅ ¡Éxito! {producto} guardado en {categoria}.")
    
    except gspread.exceptions.WorksheetNotFound:
        await update.message.reply_text(f"❌ Error: La pestaña '{categoria}' no existe en el Excel.")
    except Exception as e:
        print(f"DEBUG ERROR: {e}") # Esto aparecerá en los logs de Render
        await update.message.reply_text(f"❌ Error de conexión con Google Sheets. Verifica permisos.")

    return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operación cancelada.")
    return ConversationHandler.END

# ==========================================
# 4. MAIN
# ==========================================
def main():
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    app_bot = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('ingresar', iniciar_ingreso)],
        states={
            CATEGORIA: [CallbackQueryHandler(recibir_categoria)],
            PRODUCTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_producto)],
            CANTIDAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_cantidad)],
        },
        fallbacks=[CommandHandler('cancelar', cancelar)]
    )

    app_bot.add_handler(conv_handler)
    app_bot.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Bot Activo. Usa /ingresar")))

    # Hilo para Flask (Render)
    threading.Thread(target=run_web_server, daemon=True).start()
    
    print("🤖 Bot listo.")
    app_bot.run_polling()

if __name__ == "__main__":
    main()