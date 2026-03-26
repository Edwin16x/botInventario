import os, json, threading, uuid
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    ConversationHandler, ContextTypes, filters
)
from flask import Flask

# ==========================================
# 1. TUS IDs (PEGA TUS IDs REALES AQUÍ)
# ==========================================
ID_SHEET = "1FVlZOft3MKbiJkPJVk5nbAD1sulVDXYbRFnANdDxVtw"
ID_CARPETA_DRIVE = "120Syn98NlsbxeaSGhUW10tOceV_VpvEz"

app_flask = Flask(__name__)
# Estados del Sistema ERP
MENU_PRINCIPAL, SELECCIONAR_TABLA, RECOLECTAR_DATOS, BUSCAR_ITEM = range(4)

@app_flask.route('/')
def home(): return "✅ ERP Bodega Activo"

def run_web_server():
    app_flask.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# ==========================================
# 2. CONEXIONES (SHEETS Y DRIVE)
# ==========================================
def obtener_credenciales():
    try:
        google_json = os.environ.get("GOOGLE_CREDENTIALS")
        if not google_json: return None
        return Credentials.from_service_account_info(json.loads(google_json), scopes=[
            "https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"
        ])
    except: return None

creds_globales = obtener_credenciales()
cliente_gspread = gspread.authorize(creds_globales) if creds_globales else None
servicio_drive = build('drive', 'v3', credentials=creds_globales) if creds_globales else None

# ==========================================
# 3. LÓGICA DEL MENÚ PRINCIPAL
# ==========================================
async def mostrar_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teclado = [
        [InlineKeyboardButton("📦 Registrar Entrada", callback_data='menu_registrar')],
        [InlineKeyboardButton("🔍 Buscar Producto", callback_data='menu_buscar')],
        [InlineKeyboardButton("📊 Reportes (Próximamente)", callback_data='menu_reportes')]
    ]
    texto = "🏢 *Menú Principal de Bodega*\nSelecciona una operación:"
    
    if update.message:
        await update.message.reply_text(texto, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(texto, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
    return MENU_PRINCIPAL

async def manejador_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    opcion = query.data

    if opcion == 'menu_registrar':
        try:
            libro = cliente_gspread.open_by_key(ID_SHEET)
            teclado = [[InlineKeyboardButton(f"📁 {h.title}", callback_data=f"tabla_{h.title}")] for h in libro.worksheets()]
            teclado.append([InlineKeyboardButton("🔙 Volver al Menú", callback_data='volver_menu')])
            
            await query.edit_message_text("Selecciona el inventario destino:", reply_markup=InlineKeyboardMarkup(teclado))
            return SELECCIONAR_TABLA
        except Exception as e:
            await query.edit_message_text(f"❌ Error leyendo tablas: {e}")
            return MENU_PRINCIPAL

    elif opcion == 'menu_buscar':
        teclado = [[InlineKeyboardButton("🔙 Cancelar", callback_data='volver_menu')]]
        await query.edit_message_text("🔍 *Modo Búsqueda*\nEscribe el nombre o código del producto:", reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
        return BUSCAR_ITEM

    elif opcion == 'menu_reportes':
        await query.answer("Módulo en construcción 🚧", show_alert=True)
        return MENU_PRINCIPAL

# ==========================================
# 4. REGISTRO Y AUTO-RELLENO
# ==========================================
async def seleccionar_tabla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'volver_menu':
        return await mostrar_menu(update, context)
        
    tabla_nombre = query.data.replace("tabla_", "")
    context.user_data['tabla_nombre'] = tabla_nombre
    hoja = cliente_gspread.open_by_key(ID_SHEET).worksheet(tabla_nombre)
    encabezados = hoja.row_values(1)
    
    columnas_a_preguntar = []
    for col in encabezados:
        col_u = col.upper().strip()
        # Filtro de auto-relleno: Ignora ID, Usuario y Fecha
        if not ("USUARIO" in col_u or "FECHA" in col_u or col_u == "ID"):
            columnas_a_preguntar.append(col)
            
    context.user_data['encabezados_completos'] = encabezados
    context.user_data['columnas_preguntar'] = columnas_a_preguntar
    context.user_data['respuestas'] = {}
    context.user_data['indice'] = 0
    
    if not columnas_a_preguntar:
        await query.edit_message_text("❌ La tabla no tiene columnas válidas.")
        return MENU_PRINCIPAL

    primera_col = columnas_a_preguntar[0]
    extra = " 📸 (Envía una foto)" if primera_col.upper() == "FOTO" else ""
    await query.edit_message_text(f"📝 Tabla: *{tabla_nombre}*\n\nIntroduce: *{primera_col}*{extra}", parse_mode='Markdown')
    return RECOLECTAR_DATOS

async def recolectar_datos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cols = context.user_data['columnas_preguntar']
    idx = context.user_data['indice']
    col_actual = cols[idx]
    
    # --- PROCESAMIENTO DE FOTO ---
    if col_actual.upper() == "FOTO":
        if not update.message.photo:
            await update.message.reply_text("⚠️ Campo obligatorio: Envía una FOTO desde tu galería o cámara.")
            return RECOLECTAR_DATOS
            
        await update.message.reply_text("⏳ Subiendo imagen a Google Drive...")
        try:
            archivo = await update.message.photo[-1].get_file()
            ruta = f"temp_{uuid.uuid4().hex}.jpg"
            await archivo.download_to_drive(ruta)
            
            meta = {'name': f"Inventario_{uuid.uuid4().hex[:6]}.jpg", 'parents': [ID_CARPETA_DRIVE]}
            media = MediaFileUpload(ruta, mimetype='image/jpeg')
            res = servicio_drive.files().create(body=meta, media_body=media, fields='id, webViewLink').execute()
            servicio_drive.permissions().create(fileId=res.get('id'), body={'type': 'anyone', 'role': 'reader'}).execute()
            
            context.user_data['respuestas'][col_actual] = res.get('webViewLink')
            os.remove(ruta)
        except Exception as e:
            await update.message.reply_text(f"❌ Error subiendo foto: {e}")
            return RECOLECTAR_DATOS
            
    # --- PROCESAMIENTO DE TEXTO ---
    else:
        if not update.message.text:
            await update.message.reply_text("⚠️ Necesito TEXTO.")
            return RECOLECTAR_DATOS
        context.user_data['respuestas'][col_actual] = update.message.text

    # --- AVANZAR O GUARDAR ---
    if idx + 1 < len(cols):
        context.user_data['indice'] += 1
        sig = cols[idx + 1]
        extra = " 📸 (Envía una foto)" if sig.upper() == "FOTO" else ""
        await update.message.reply_text(f"Siguiente dato: *{sig}*{extra}", parse_mode='Markdown')
        return RECOLECTAR_DATOS
    else:
        await update.message.reply_text("⏳ Ensamblando datos y guardando en Sheets...")
        
        fila_final = []
        for col in context.user_data['encabezados_completos']:
            col_u = col.upper().strip()
            # Mapeo de auto-relleno
            if "USUARIO" in col_u:
                fila_final.append(update.message.from_user.username or update.message.from_user.first_name)
            elif "FECHA" in col_u:
                fila_final.append(datetime.now().strftime("%Y-%m-%d %H:%M"))
            elif col_u == "ID":
                fila_final.append(str(uuid.uuid4().hex[:6]).upper())
            else:
                fila_final.append(context.user_data['respuestas'].get(col, ""))
                
        try:
            hoja = cliente_gspread.open_by_key(ID_SHEET).worksheet(context.user_data['tabla_nombre'])
            hoja.append_row(fila_final)
            await update.message.reply_text("✅ ¡Registro completado!\nUsa /menu para otra operación.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error de escritura: {e}")
            
        return ConversationHandler.END

# ==========================================
# 5. MÓDULO DE BÚSQUEDA
# ==========================================
async def buscar_item_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    termino = update.message.text.lower()
    await update.message.reply_text(f"⏳ Rastreando '{termino}' en bodega...")
    
    try:
        libro = cliente_gspread.open_by_key(ID_SHEET)
        resultados = []
        
        for hoja in libro.worksheets():
            registros = hoja.get_all_values()
            if not registros: continue
            encabezados = registros[0]
            
            for fila in registros[1:]:
                if any(termino in celda.lower() for celda in fila):
                    res_texto = f"📌 *Encontrado en:* {hoja.title}\n"
                    for i in range(len(fila)):
                        if i < len(encabezados) and fila[i]:
                            # Si es un enlace de foto, lo mostramos limpio
                            if "http" in fila[i] and "drive" in fila[i]:
                                res_texto += f"- {encabezados[i]}: [Ver Foto]({fila[i]})\n"
                            else:
                                res_texto += f"- {encabezados[i]}: {fila[i]}\n"
                    resultados.append(res_texto)
        
        if resultados:
            for r in resultados[:3]:
                await update.message.reply_text(r, parse_mode='Markdown', disable_web_page_preview=True)
            if len(resultados) > 3:
                await update.message.reply_text(f"*(Mostrando 3 de {len(resultados)} resultados)*", parse_mode='Markdown')
        else:
            await update.message.reply_text("❌ Producto no encontrado.")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Error buscando: {e}")
        
    return ConversationHandler.END

# ==========================================
# MAIN
# ==========================================
def main():
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', mostrar_menu), CommandHandler('menu', mostrar_menu)],
        states={
            MENU_PRINCIPAL: [CallbackQueryHandler(manejador_menu)],
            SELECCIONAR_TABLA: [CallbackQueryHandler(seleccionar_tabla)],
            RECOLECTAR_DATOS: [MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, recolectar_datos)],
            BUSCAR_ITEM: [
                CallbackQueryHandler(mostrar_menu, pattern='^volver_menu$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, buscar_item_handler)
            ]
        },
        fallbacks=[CommandHandler('menu', mostrar_menu)]
    )

    app.add_handler(conv_handler)
    threading.Thread(target=run_web_server, daemon=True).start()
    app.run_polling()

if __name__ == "__main__":
    main()