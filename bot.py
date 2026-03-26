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
# 1. SERVIDOR WEB (Para evitar que Render apague el bot)
# ==========================================
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "✅ Servicio de Inventario Activo y Corriendo."

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host="0.0.0.0", port=port)

# ==========================================
# 2. CONEXIÓN A GOOGLE SHEETS
# ==========================================
def conectar_sheets():
    # Lee el JSON desde las variables de entorno de Render
    google_credentials_json = os.environ.get("GOOGLE_CREDENTIALS")
    
    if not google_credentials_json:
        print("⚠️ ERROR: No se encontró la variable GOOGLE_CREDENTIALS.")
        return None

    creds_dict = json.loads(google_credentials_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ])
    return gspread.authorize(creds)

cliente_gspread = conectar_sheets()
# IMPORTANTE: Reemplaza esto con el nombre exacto de tu archivo en Google Drive
NOMBRE_ARCHIVO_SHEETS = "Inventario_Residencias" 

# ==========================================
# 3. MÁQUINA DE ESTADOS (TELEGRAM)
# ==========================================
CATEGORIA, PRODUCTO, CANTIDAD = range(3)

async def iniciar_ingreso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teclado = [
        [InlineKeyboardButton("🥾 Calzado", callback_data='Calzado')],
        [InlineKeyboardButton("🔧 Herramientas", callback_data='Herramientas')],
        [InlineKeyboardButton("🔌 Electrónica", callback_data='Electronica')]
    ]
    reply_markup = InlineKeyboardMarkup(teclado)
    
    await update.message.reply_text(
        '📦 *Nuevo Ingreso*\n¿En qué categoría vas a registrar?', 
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return CATEGORIA

async def recibir_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() 
    
    context.user_data['categoria'] = query.data 
    await query.edit_message_text(text=f"Elegiste: *{query.data}*.\nAhora, escribe el *nombre o código* del producto:", parse_mode='Markdown')
    return PRODUCTO

async def recibir_producto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['producto'] = update.message.text
    await update.message.reply_text("Entendido. Ahora, ingresa la *cantidad* (solo números):", parse_mode='Markdown')
    return CANTIDAD

async def recibir_cantidad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cantidad = update.message.text
    
    if not cantidad.isdigit():
        await update.message.reply_text("⚠️ Error: La cantidad debe ser un número entero. Inténtalo de nuevo:")
        return CANTIDAD
        
    producto = context.user_data['producto']
    categoria = context.user_data['categoria'] 
    usuario = update.message.from_user.username or update.message.from_user.first_name

    await update.message.reply_text("⏳ Guardando en la base de datos...")

    try:
        if not cliente_gspread:
            raise Exception("No hay conexión con Google Sheets.")
            
        hoja_activa = cliente_gspread.open(NOMBRE_ARCHIVO_SHEETS).worksheet(categoria)
        
        # Insertamos: ID/Código, Producto, Cantidad, Usuario, Estado/Ubicación
        hoja_activa.append_row(["AUTOGENERADO", producto, int(cantidad), usuario, "Pendiente"])
        
        await update.message.reply_text(f"✅ ¡Éxito! Registraste {cantidad}x {producto} en la tabla de {categoria}.")
    
    except gspread.exceptions.WorksheetNotFound:
        await update.message.reply_text(f"❌ Error: No existe una pestaña llamada '{categoria}' en tu Excel.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error crítico: {e}")

    return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛑 Operación cancelada. Usa /ingresar para empezar de nuevo.")
    return ConversationHandler.END

# ==========================================
# 4. ARRANQUE DEL SISTEMA
# ==========================================
def main():
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    if not TOKEN:
        print("⚠️ ERROR: No se encontró la variable TELEGRAM_TOKEN.")
        return

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
    
    # Arrancamos Flask en un hilo secundario
    hilo_web = threading.Thread(target=run_web_server)
    hilo_web.daemon = True
    hilo_web.start()
    
    print("🤖 Bot iniciado y en modo escucha...")
    app_bot.run_polling()

if __name__ == "__main__":
    main()