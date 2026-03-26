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
# Nuevos Estados
MENU_PRINCIPAL, SELECCIONAR_TABLA, RECOLECTAR_DATOS, BUSCAR_ITEM, ESPERAR_CANTIDAD = range(5)

@app_flask.route('/')
def home(): return "✅ ERP Bodega V4 Activo"

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
        [InlineKeyboardButton("📦 Registrar Entrada Nueva", callback_data='menu_registrar')],
        [InlineKeyboardButton("🔍 Buscar / Gestionar Stock", callback_data='menu_buscar')]
    ]
    texto = "🏢 *ERP INVENTARIO - RESIDENCIAS*\nSelecciona una acción:"
    
    if update.message:
        await update.message.reply_text(texto, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(texto, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
    return MENU_PRINCIPAL

async def manejador_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'volver_menu': return await mostrar_menu(update, context)
    
    if query.data == 'menu_registrar':
        libro = cliente_gspread.open_by_key(ID_SHEET)
        teclado = [[InlineKeyboardButton(f"📁 {h.title}", callback_data=f"tabla_{h.title}")] for h in libro.worksheets()]
        teclado.append([InlineKeyboardButton("🔙 Volver al Menú", callback_data='volver_menu')])
        await query.edit_message_text("Selecciona el inventario destino:", reply_markup=InlineKeyboardMarkup(teclado))
        return SELECCIONAR_TABLA

    elif query.data == 'menu_buscar':
        teclado = [[InlineKeyboardButton("🔙 Volver al Menú", callback_data='volver_menu')]]
        await query.edit_message_text("🔍 Escribe el NOMBRE o CÓDIGO del producto a buscar:", reply_markup=InlineKeyboardMarkup(teclado))
        return BUSCAR_ITEM

# ==========================================
# 4. REGISTRO
# ==========================================
async def seleccionar_tabla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'volver_menu': return await mostrar_menu(update, context)
    
    tabla = query.data.replace("tabla_", "")
    context.user_data['tabla'] = tabla
    hoja = cliente_gspread.open_by_key(ID_SHEET).worksheet(tabla)
    encabezados = hoja.row_values(1)
    
    # Filtro: Excluir explícitamente estas palabras para que SÍ te pida Cantidad
    palabras_omitir = ["USUARIO", "FECHA", "ID", "MODIFICADO"]
    preguntas = [c for c in encabezados if not any(p in c.upper() for p in palabras_omitir)]
    
    if not preguntas:
        await query.edit_message_text("❌ No hay columnas válidas para registrar.")
        return await mostrar_menu(update, context)

    context.user_data.update({'headers': encabezados, 'preguntas': preguntas, 'respuestas': {}, 'idx': 0})
    await query.edit_message_text(f"📝 *Registro en {tabla}*\nVamos a pedirte: {', '.join(preguntas)}\n\nIntroduce: *{preguntas[0]}*", parse_mode='Markdown')
    return RECOLECTAR_DATOS

async def recolectar_datos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    preguntas = context.user_data['preguntas']
    idx = context.user_data['idx']
    col = preguntas[idx]
    
    if col.upper() == "FOTO":
        if not update.message.photo:
            await update.message.reply_text("⚠️ Envía una FOTO para este campo.")
            return RECOLECTAR_DATOS
        context.user_data['respuestas'][col] = update.message.photo[-1].file_id
    else:
        context.user_data['respuestas'][col] = update.message.text

    if idx + 1 < len(preguntas):
        context.user_data['idx'] += 1
        await update.message.reply_text(f"Siguiente: *{preguntas[idx+1]}*", parse_mode='Markdown')
        return RECOLECTAR_DATOS
    else:
        await update.message.reply_text("⏳ Procesando...")
        fila = []
        user = update.message.from_user
        nombre_real = f"@{user.username}" if user.username else user.first_name
        ahora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        for h in context.user_data['headers']:
            h_u = h.upper()
            if "USUARIO" in h_u: fila.append(nombre_real)
            elif "FECHA" in h_u or "MODIFICADO" in h_u: fila.append(ahora)
            elif h_u == "ID": fila.append(str(uuid.uuid4().hex[:6]).upper())
            else: fila.append(context.user_data['respuestas'].get(h, "0"))
            
        cliente_gspread.open_by_key(ID_SHEET).worksheet(context.user_data['tabla']).append_row(fila)
        await update.message.reply_text(f"✅ ¡Guardado con éxito!")
        return await mostrar_menu(update, context)

# ==========================================
# 5. BÚSQUEDA Y MODIFICACIÓN EN BLOQUE
# ==========================================
async def buscar_item_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    termino = update.message.text.lower()
    await update.message.reply_text(f"⏳ Buscando '{termino}'... (Esto toma unos segundos por Google Sheets)")
    
    libro = cliente_gspread.open_by_key(ID_SHEET)
    encontrado = False

    for hoja in libro.worksheets():
        registros = hoja.get_all_records()
        for i, fila in enumerate(registros):
            if any(termino in str(val).lower() for val in fila.values()):
                encontrado = True
                info = f"📦 *{hoja.title}* (Fila {i+2})\n"
                foto_id = None
                
                for k, v in fila.items():
                    if k.upper() == "FOTO": foto_id = v
                    else: info += f"• *{k}:* {v}\n"
                
                teclado = []
                # Si existe la columna que contiene "CANTIDAD", habilitamos los botones de modificación
                if any("CANTIDAD" in k.upper() for k in fila.keys()):
                    teclado = [
                        [InlineKeyboardButton("➕ Ingresar Stock", callback_data=f"mod|add|{hoja.title}|{i+2}")],
                        [InlineKeyboardButton("➖ Retirar Stock", callback_data=f"mod|sub|{hoja.title}|{i+2}")]
                    ]
                teclado.append([InlineKeyboardButton("🔙 Volver al Menú", callback_data='volver_menu')])
                
                if foto_id:
                    try: await update.message.reply_photo(photo=foto_id, caption=info, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
                    except: await update.message.reply_text(info + "\n*(Foto no disponible)*", reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
                else:
                    await update.message.reply_text(info, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')

    if not encontrado:
        teclado = [[InlineKeyboardButton("🔙 Volver al Menú", callback_data='volver_menu')]]
        await update.message.reply_text(f"❌ No encontré '{termino}'.", reply_markup=InlineKeyboardMarkup(teclado))
    
    return BUSCAR_ITEM # Nos quedamos en este estado para escuchar los botones

async def preparar_modificacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'volver_menu': return await mostrar_menu(update, context)
    
    # Extraemos los datos: mod | accion | tabla | fila
    datos = query.data.split('|')
    context.user_data['mod_data'] = {'accion': datos[1], 'tabla': datos[2], 'fila': int(datos[3])}
    
    accion_texto = "INGRESAR" if datos[1] == 'add' else "RETIRAR"
    
    teclado = [[InlineKeyboardButton("🔙 Cancelar y Volver", callback_data='volver_menu')]]
    await query.edit_message_caption(caption=f"⚙️ Vas a *{accion_texto}* stock en la fila {datos[3]}.\n\nEscribe el número de unidades (ej. 15):", reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown') if query.message.photo else await query.edit_message_text(f"⚙️ Vas a *{accion_texto}* stock en la fila {datos[3]}.\n\nEscribe el número de unidades (ej. 15):", reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
    
    return ESPERAR_CANTIDAD

async def procesar_modificacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isdigit():
        await update.message.reply_text("⚠️ Por favor, escribe solo un número válido.")
        return ESPERAR_CANTIDAD
        
    cantidad_modificar = int(update.message.text)
    datos = context.user_data['mod_data']
    hoja = cliente_gspread.open_by_key(ID_SHEET).worksheet(datos['tabla'])
    encabezados = hoja.row_values(1)
    
    idx_cant, idx_mod = -1, -1
    for i, h in enumerate(encabezados):
        if "CANTIDAD" in h.upper(): idx_cant = i + 1
        if "MODIFICADO" in h.upper() or "FECHA" in h.upper(): idx_mod = i + 1

    if idx_cant != -1:
        await update.message.reply_text("⏳ Actualizando inventario...")
        valor_actual = hoja.cell(datos['fila'], idx_cant).value
        valor_actual = int(valor_actual) if valor_actual and str(valor_actual).isdigit() else 0
        
        nuevo_valor = valor_actual + cantidad_modificar if datos['accion'] == 'add' else valor_actual - cantidad_modificar
        if nuevo_valor < 0: nuevo_valor = 0 # Evitar stock negativo
        
        hoja.update_cell(datos['fila'], idx_cant, nuevo_valor)
        
        if idx_mod != -1:
            hoja.update_cell(datos['fila'], idx_mod, datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
            
        await update.message.reply_text(f"✅ ¡Listo! El stock se actualizó de {valor_actual} a *{nuevo_valor}*.", parse_mode='Markdown')
    
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
            BUSCAR_ITEM: [
                CallbackQueryHandler(preparar_modificacion, pattern='^mod|'),
                CallbackQueryHandler(manejador_menu, pattern='^volver_menu$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, buscar_item_handler)
            ],
            ESPERAR_CANTIDAD: [
                CallbackQueryHandler(manejador_menu, pattern='^volver_menu$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_modificacion)
            ]
        },
        fallbacks=[CommandHandler('menu', mostrar_menu)]
    )
    
    app.add_handler(conv)
    threading.Thread(target=run_web_server, daemon=True).start()
    app.run_polling()

if __name__ == "__main__": main()