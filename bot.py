import os, json, threading, uuid
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    ConversationHandler, ContextTypes, filters
)
from flask import Flask

# ==========================================
# 1. CONFIGURACIÓN
# ==========================================
ID_SHEET = "1FVlZOft3MKbiJkPJVk5nbAD1sulVDXYbRFnANdDxVtw"

app_flask = Flask(__name__)
MENU_PRINCIPAL, SELECCIONAR_TABLA, RECOLECTAR_DATOS, BUSCAR_ITEM = range(4)

@app_flask.route('/')
def home(): return "✅ ERP Bodega Activo (Modo Velocidad)"

def run_web_server():
    app_flask.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# ==========================================
# 2. CONEXIÓN A SHEETS
# ==========================================
def obtener_credenciales():
    try:
        google_json = os.environ.get("GOOGLE_CREDENTIALS")
        return Credentials.from_service_account_info(json.loads(google_json), 
            scopes=["https://www.googleapis.com/auth/spreadsheets"])
    except: return None

creds_globales = obtener_credenciales()
cliente_gspread = gspread.authorize(creds_globales) if creds_globales else None

# ==========================================
# 3. MENÚ Y NAVEGACIÓN
# ==========================================
async def mostrar_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teclado = [
        [InlineKeyboardButton("📦 Registrar Entrada", callback_data='menu_registrar')],
        [InlineKeyboardButton("🔍 Buscar Producto", callback_data='menu_buscar')]
    ]
    texto = "🏢 *Control de Inventario*\nModo: Residencia Agilizada"
    
    if update.message:
        await update.message.reply_text(texto, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(texto, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
    return MENU_PRINCIPAL

async def manejador_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'menu_registrar':
        libro = cliente_gspread.open_by_key(ID_SHEET)
        teclado = [[InlineKeyboardButton(f"📁 {h.title}", callback_data=f"tabla_{h.title}")] for h in libro.worksheets()]
        teclado.append([InlineKeyboardButton("🔙 Volver", callback_data='volver_menu')])
        await query.edit_message_text("Selecciona inventario:", reply_markup=InlineKeyboardMarkup(teclado))
        return SELECCIONAR_TABLA

    elif query.data == 'menu_buscar':
        await query.edit_message_text("🔍 Escribe el nombre o código del producto:")
        return BUSCAR_ITEM

# ==========================================
# 4. REGISTRO ULTRA-RÁPIDO
# ==========================================
async def seleccionar_tabla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'volver_menu': return await mostrar_menu(update, context)
    
    tabla = query.data.replace("tabla_", "")
    context.user_data['tabla'] = tabla
    encabezados = cliente_gspread.open_by_key(ID_SHEET).worksheet(tabla).row_values(1)
    
    # Filtrar columnas automáticas
    preguntas = [c for c in encabezados if not any(x in c.upper() for x in ["USUARIO", "FECHA", "ID"])]
    
    context.user_data.update({'headers': encabezados, 'preguntas': preguntas, 'respuestas': {}, 'idx': 0})
    
    await query.edit_message_text(f"📝 {tabla}\nIntroduce: *{preguntas[0]}*", parse_mode='Markdown')
    return RECOLECTAR_DATOS

async def recolectar_datos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    preguntas = context.user_data['preguntas']
    idx = context.user_data['idx']
    col = preguntas[idx]

    # Guardar dato (Foto o Texto)
    if col.upper() == "FOTO":
        if not update.message.photo:
            await update.message.reply_text("⚠️ Envía una FOTO.")
            return RECOLECTAR_DATOS
        context.user_data['respuestas'][col] = update.message.photo[-1].file_id
    else:
        context.user_data['respuestas'][col] = update.message.text or "N/A"

    # Siguiente o Finalizar
    if idx + 1 < len(preguntas):
        context.user_data['idx'] += 1
        await update.message.reply_text(f"Siguiente: *{preguntas[idx+1]}*", parse_mode='Markdown')
        return RECOLECTAR_DATOS
    else:
        # Guardar en Sheets
        fila = []
        for h in context.user_data['headers']:
            h_u = h.upper()
            if "USUARIO" in h_u: fila.append(update.message.from_user.username or "Anon")
            elif "FECHA" in h_u: fila.append(datetime.now().strftime("%d/%m/%Y %H:%M"))
            elif h_u == "ID": fila.append(str(uuid.uuid4().hex[:6]).upper())
            else: fila.append(context.user_data['respuestas'].get(h, ""))
            
        cliente_gspread.open_by_key(ID_SHEET).worksheet(context.user_data['tabla']).append_row(fila)
        await update.message.reply_text("✅ Guardado al instante.")
        return await mostrar_menu(update, context)

# ==========================================
# 5. BÚSQUEDA CON RECUPERACIÓN DE FOTO
# ==========================================
async def buscar_item_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    termino = update.message.text.lower()
    libro = cliente_gspread.open_by_key(ID_SHEET)
    encontrado = False

    for hoja in libro.worksheets():
        data = hoja.get_all_records()
        for fila in data:
            if any(termino in str(val).lower() for val in fila.values()):
                encontrado = True
                info = f"📌 *{hoja.title}*\n"
                foto_id = None
                
                for k, v in fila.items():
                    if k.upper() == "FOTO": foto_id = v
                    else: info += f"• {k}: {v}\n"
                
                if foto_id:
                    try: await update.message.reply_photo(photo=foto_id, caption=info, parse_mode='Markdown')
                    except: await update.message.reply_text(info + "\n(Foto no disponible)", parse_mode='Markdown')
                else:
                    await update.message.reply_text(info, parse_mode='Markdown')

    if not encontrado: await update.message.reply_text("❌ Sin resultados.")
    return await mostrar_menu(update, context)

# ==========================================
# MAIN
# ==========================================
def main():
    app = Application.builder().token(os.environ.get("TELEGRAM_TOKEN")).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', mostrar_menu), CommandHandler('menu', mostrar_menu)],
        states={
            MENU_PRINCIPAL: [CallbackQueryHandler(manejador_menu)],
            SELECCIONAR_TABLA: [CallbackQueryHandler(seleccionar_tabla)],
            RECOLECTAR_DATOS: [MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, recolectar_datos)],
            BUSCAR_ITEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, buscar_item_handler)]
        },
        fallbacks=[CommandHandler('menu', mostrar_menu)]
    )
    app.add_handler(conv)
    threading.Thread(target=run_web_server, daemon=True).start()
    app.run_polling()

if __name__ == "__main__": main()