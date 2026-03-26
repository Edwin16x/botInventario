import os
import json
import threading
import uuid
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
# 1. CONFIGURACIÓN (TUS IDs)
# ==========================================
ID_SHEET = "1FVlZOft3MKbiJkPJVk5nbAD1sulVDXYbRFnANdDxVtw"
ID_CARPETA_DRIVE = "120Syn98NlsbxeaSGhUW10tOceV_VpvEz"

app_flask = Flask(__name__)
SELECCIONAR_TABLA, RECOLECTAR_DATOS = range(2)

@app_flask.route('/')
def home(): return "✅ Sistema ERP / Inventario Activo."

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port)

# ==========================================
# 2. CONEXIONES A GOOGLE (SHEETS Y DRIVE)
# ==========================================
def obtener_credenciales():
    try:
        google_json = os.environ.get("GOOGLE_CREDENTIALS")
        if not google_json: return None
        return Credentials.from_service_account_info(json.loads(google_json), scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ])
    except: return None

creds_globales = obtener_credenciales()
cliente_gspread = gspread.authorize(creds_globales) if creds_globales else None
# Iniciamos el servicio de Drive
servicio_drive = build('drive', 'v3', credentials=creds_globales) if creds_globales else None

# ==========================================
# 3. LÓGICA DINÁMICA DE REGISTRO
# ==========================================
async def iniciar_registro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        libro = cliente_gspread.open_by_key(ID_SHEET)
        teclado = [[InlineKeyboardButton(f"📁 {h.title}", callback_data=h.title)] for h in libro.worksheets()]
        await update.message.reply_text("📦 *Nuevo Registro*\nSelecciona la tabla:", reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
        return SELECCIONAR_TABLA
    except Exception as e:
        await update.message.reply_text(f"❌ Error conectando a Sheets: {e}")
        return ConversationHandler.END

async def seleccionar_tabla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    tabla_nombre = query.data
    context.user_data['tabla_nombre'] = tabla_nombre
    
    hoja = cliente_gspread.open_by_key(ID_SHEET).worksheet(tabla_nombre)
    encabezados = hoja.row_values(1)
    
    # Separar columnas manuales de las automáticas
    columnas_a_preguntar = []
    for col in encabezados:
        col_upper = col.upper()
        if col_upper not in ["USUARIO", "FECHA", "ID"]:
            columnas_a_preguntar.append(col)
            
    context.user_data['encabezados_completos'] = encabezados
    context.user_data['columnas_preguntar'] = columnas_a_preguntar
    context.user_data['respuestas_manuales'] = {}
    context.user_data['indice'] = 0
    
    if not columnas_a_preguntar:
        await query.edit_message_text("❌ No hay columnas configuradas para rellenar.")
        return ConversationHandler.END

    primera_col = columnas_a_preguntar[0]
    mensaje_extra = " 📸 (Envía una imagen)" if primera_col.upper() == "FOTO" else ""
    await query.edit_message_text(f"📝 Tabla: *{tabla_nombre}*\nIntroduce: *{primera_col}*{mensaje_extra}", parse_mode='Markdown')
    return RECOLECTAR_DATOS

async def recolectar_datos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    columnas_preguntas = context.user_data['columnas_preguntar']
    idx = context.user_data['indice']
    columna_actual = columnas_preguntas[idx]
    
    # 1. VALIDACIÓN: ¿Pide foto o pide texto?
    if columna_actual.upper() == "FOTO":
        if not update.message.photo:
            await update.message.reply_text("⚠️ Este campo requiere una FOTO. Por favor, envía una imagen.")
            return RECOLECTAR_DATOS
            
        await update.message.reply_text("⏳ Subiendo imagen a Drive...")
        
        # Descargar foto de Telegram
        archivo_foto = await update.message.photo[-1].get_file()
        ruta_temporal = f"temp_{uuid.uuid4().hex}.jpg"
        await archivo_foto.download_to_drive(ruta_temporal)
        
        # Subir a Google Drive
        metadatos = {'name': f"Inventario_{uuid.uuid4().hex[:6]}.jpg", 'parents': [ID_CARPETA_DRIVE]}
        media = MediaFileUpload(ruta_temporal, mimetype='image/jpeg', resumable=True)
        archivo_drive = servicio_drive.files().create(body=metadatos, media_body=media, fields='id, webViewLink').execute()
        
        # Dar permisos públicos de lectura para que el bot la pueda mandar después
        servicio_drive.permissions().create(fileId=archivo_drive.get('id'), body={'type': 'anyone', 'role': 'reader'}).execute()
        
        # Guardar enlace y limpiar
        context.user_data['respuestas_manuales'][columna_actual] = archivo_drive.get('webViewLink')
        os.remove(ruta_temporal)
        
    else:
        if not update.message.text:
            await update.message.reply_text("⚠️ Este campo requiere TEXTO. Por favor, escribe el dato.")
            return RECOLECTAR_DATOS
        context.user_data['respuestas_manuales'][columna_actual] = update.message.text

    # 2. AVANZAR A LA SIGUIENTE PREGUNTA O GUARDAR
    if idx + 1 < len(columnas_preguntas):
        context.user_data['indice'] += 1
        siguiente_col = columnas_preguntas[idx + 1]
        mensaje_extra = " 📸 (Envía una imagen)" if siguiente_col.upper() == "FOTO" else ""
        await update.message.reply_text(f"Siguiente: *{siguiente_col}*{mensaje_extra}", parse_mode='Markdown')
        return RECOLECTAR_DATOS
    else:
        await update.message.reply_text("⏳ Construyendo registro final...")
        
        # 3. ENSAMBLAR FILA (Mezclando manuales y automáticos)
        fila_final = []
        encabezados_completos = context.user_data['encabezados_completos']
        
        for col in encabezados_completos:
            col_u = col.upper()
            if col_u == "USUARIO":
                fila_final.append(update.message.from_user.username or update.message.from_user.first_name)
            elif col_u == "FECHA":
                fila_final.append(datetime.now().strftime("%Y-%m-%d %H:%M"))
            elif col_u == "ID":
                fila_final.append(str(uuid.uuid4().hex[:8]).upper())
            else:
                fila_final.append(context.user_data['respuestas_manuales'].get(col, ""))
                
        # Escribir en Sheets
        try:
            hoja = cliente_gspread.open_by_key(ID_SHEET).worksheet(context.user_data['tabla_nombre'])
            hoja.append_row(fila_final)
            await update.message.reply_text("✅ ¡Registro completado y guardado con éxito!")
        except Exception as e:
            await update.message.reply_text(f"❌ Error al escribir en Excel: {e}")
            
        return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛑 Cancelado.")
    return ConversationHandler.END

# ==========================================
# 4. MAIN
# ==========================================
def main():
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    app = Application.builder().token(TOKEN).build()

    # IMPORTANTE: Ahora el MessageHandler acepta TEXTO y FOTOS
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('ingresar', iniciar_registro)],
        states={
            SELECCIONAR_TABLA: [CallbackQueryHandler(seleccionar_tabla)],
            RECOLECTAR_DATOS: [MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, recolectar_datos)],
        },
        fallbacks=[CommandHandler('cancelar', cancelar)]
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("ERP Activo. Usa /ingresar")))

    threading.Thread(target=run_web_server, daemon=True).start()
    app.run_polling()

if __name__ == "__main__":
    main()