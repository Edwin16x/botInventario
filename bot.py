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
MENU_PRINCIPAL, SELECCIONAR_TABLA, RECOLECTAR_DATOS, BUSCAR_ITEM, MODIFICAR_STOCK = range(5)

@app_flask.route('/')
def home(): return "✅ ERP Bodega V3 Activo"

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
# 3. INTERFAZ Y NAVEGACIÓN
# ==========================================
async def mostrar_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teclado = [
        [InlineKeyboardButton("📦 Registrar Entrada", callback_data='menu_registrar')],
        [InlineKeyboardButton("🔍 Buscar / Gestionar Stock", callback_data='menu_buscar')]
    ]
    texto = "🏢 *ERP INVENTARIO - RESIDENCIAS*\nGestión de bodega en tiempo real."
    
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
        await query.edit_message_text("🔍 Escribe el NOMBRE o CÓDIGO del producto a buscar:")
        return BUSCAR_ITEM

# ==========================================
# 4. REGISTRO CON TRAZABILIDAD
# ==========================================
async def seleccionar_tabla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'volver_menu': return await mostrar_menu(update, context)
    
    tabla = query.data.replace("tabla_", "")
    context.user_data['tabla'] = tabla
    hoja = cliente_gspread.open_by_key(ID_SHEET).worksheet(tabla)
    encabezados = hoja.row_values(1)
    
    # Filtro inteligente de columnas automáticas
    preguntas = [c for c in encabezados if not any(x in c.upper() for x in ["USUARIO", "FECHA", "ID", "MODIFICADO"])]
    
    context.user_data.update({'headers': encabezados, 'preguntas': preguntas, 'respuestas': {}, 'idx': 0})
    await query.edit_message_text(f"📝 *Registro en {tabla}*\nIntroduce: *{preguntas[0]}*", parse_mode='Markdown')
    return RECOLECTAR_DATOS

async def recolectar_datos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    preguntas = context.user_data['preguntas']
    idx = context.user_data['idx']
    col = preguntas[idx]
    user = update.message.from_user
    nombre_real = f"@{user.username}" if user.username else f"{user.first_name} {user.last_name or ''}"

    # Captura de datos
    if col.upper() == "FOTO":
        if not update.message.photo:
            await update.message.reply_text("⚠️ Error: Envía una FOTO para este campo.")
            return RECOLECTAR_DATOS
        context.user_data['respuestas'][col] = update.message.photo[-1].file_id
    else:
        context.user_data['respuestas'][col] = update.message.text

    # Siguiente pregunta o Guardar
    if idx + 1 < len(preguntas):
        context.user_data['idx'] += 1
        await update.message.reply_text(f"Siguiente: *{preguntas[idx+1]}*", parse_mode='Markdown')
        return RECOLECTAR_DATOS
    else:
        await update.message.reply_text("⏳ Procesando registro...")
        fila = []
        ahora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        for h in context.user_data['headers']:
            h_u = h.upper()
            if "USUARIO" in h_u: fila.append(nombre_real)
            elif "FECHA" in h_u or "MODIFICADO" in h_u: fila.append(ahora)
            elif h_u == "ID": fila.append(str(uuid.uuid4().hex[:6]).upper())
            else: fila.append(context.user_data['respuestas'].get(h, "N/A"))
            
        cliente_gspread.open_by_key(ID_SHEET).worksheet(context.user_data['tabla']).append_row(fila)
        await update.message.reply_text(f"✅ Guardado por {nombre_real} a las {ahora}")
        return await mostrar_menu(update, context)

# ==========================================
# 5. BÚSQUEDA Y GESTIÓN DE STOCK
# ==========================================
async def buscar_item_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    termino = update.message.text.lower()
    libro = cliente_gspread.open_by_key(ID_SHEET)
    encontrado = False

    for hoja in libro.worksheets():
        registros = hoja.get_all_records() # Obtiene diccionarios llave:valor
        for i, fila in enumerate(registros):
            # i+2 es la fila real en Excel (1-based + encabezado)
            if any(termino in str(val).lower() for val in fila.values()):
                encontrado = True
                info = f"📦 *{hoja.title}* (Fila {i+2})\n"
                foto_id = None
                
                for k, v in fila.items():
                    if k.upper() == "FOTO": foto_id = v
                    else: info += f"• *{k}:* {v}\n"
                
                # Botones de acción para stock (Si existe columna Cantidad)
                teclado = []
                if "Cantidad" in fila:
                    teclado = [[
                        InlineKeyboardButton("➕ Sumar 1", callback_data=f"stock|add|{hoja.title}|{i+2}"),
                        InlineKeyboardButton("➖ Restar 1", callback_data=f"stock|sub|{hoja.title}|{i+2}")
                    ]]
                
                if foto_id:
                    await update.message.reply_photo(photo=foto_id, caption=info, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
                else:
                    await update.message.reply_text(info, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')

    if not encontrado:
        await update.message.reply_text(f"❌ No encontré '{termino}' en ningún inventario.")
    return MENU_PRINCIPAL # Mantiene el estado en el menú

async def gestion_stock_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data.split('|') # stock | accion | tabla | fila
    accion, tabla_nombre, num_fila = data[1], data[2], int(data[3])
    
    hoja = cliente_gspread.open_by_key(ID_SHEET).worksheet(tabla_nombre)
    encabezados = hoja.row_values(1)
    
    # Buscar índice de columna Cantidad
    idx_cant = -1
    idx_mod = -1
    for i, h in enumerate(encabezados):
        if "CANTIDAD" in h.upper(): idx_cant = i + 1
        if "MODIFICADO" in h.upper() or "FECHA" in h.upper(): idx_mod = i + 1

    if idx_cant != -1:
        valor_actual = int(hoja.cell(num_fila, idx_cant).value or 0)
        nuevo_valor = valor_actual + 1 if accion == 'add' else valor_actual - 1
        if nuevo_valor < 0: nuevo_valor = 0
        
        hoja.update_cell(num_fila, idx_cant, nuevo_valor)
        
        # Actualizar Timestamp de modificación
        if idx_mod != -1:
            hoja.update_cell(num_fila, idx_mod, datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
            
        await query.answer(f"Stock actualizado: {nuevo_valor}")
        # Editar el mensaje para reflejar el cambio (Opcional, requiere re-generar el texto)
        await query.edit_message_caption(caption=f"✅ Cantidad actualizada a: {nuevo_valor}\nUsa /menu para volver.") if query.message.photo else await query.edit_message_text(text=f"✅ Cantidad actualizada a: {nuevo_valor}\nUsa /menu para volver.")
    
    return MENU_PRINCIPAL

# ==========================================
# MAIN
# ==========================================
def main():
    app = Application.builder().token(os.environ.get("TELEGRAM_TOKEN")).build()
    
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', mostrar_menu), CommandHandler('menu', mostrar_menu)],
        states={
            MENU_PRINCIPAL: [
                CallbackQueryHandler(manejador_menu, pattern='^menu_'),
                CallbackQueryHandler(gestion_stock_callback, pattern='^stock|')
            ],
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