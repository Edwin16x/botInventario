import os, threading, uuid, io
from datetime import datetime
import pandas as pd
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    ConversationHandler, ContextTypes, filters
)
from flask import Flask

# ==========================================
# 1. CONFIGURACIÓN Y CONEXIÓN SUPABASE
# ==========================================
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key) if url and key else None

app_flask = Flask(__name__)
MENU_PRINCIPAL, ELEGIR_CATEGORIA, RECOLECTAR_DATOS, BUSCAR_ITEM, ESPERAR_CANTIDAD = range(5)

@app_flask.route('/')
def home(): return "✅ ERP Bodega V5 (SQL) Activo"

def run_web_server():
    app_flask.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# ==========================================
# 2. MOTOR DE PLANTILLAS DINÁMICAS
# ==========================================
# Los datos que TODO producto debe tener:
CAMPOS_BASE = ["Codigo", "Producto", "Cantidad", "Zona", "Foto"]

# Los datos dinámicos según lo que elijas (¡Esto se guarda en el JSONB!)
CATEGORIAS = {
    "Calzado": ["Talla", "Color", "Casquillo"],
    "Herramientas": ["Marca", "Voltaje"],
    "EPP": ["Material", "Norma_Seguridad"],
    "General": ["Observaciones"]
}

# ==========================================
# 3. INTERFAZ Y NAVEGACIÓN
# ==========================================
async def mostrar_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teclado = [
        [InlineKeyboardButton("📦 Registrar Nuevo Ingreso", callback_data='menu_registrar')],
        [InlineKeyboardButton("🔍 Buscar / Gestionar Stock", callback_data='menu_buscar')],
        [InlineKeyboardButton("📊 Generar Reporte Excel", callback_data='menu_reporte')]
    ]
    texto = "🏢 *ERP BODEGA - MODO SQL*\nBase de datos en tiempo real."
    
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
        teclado = [[InlineKeyboardButton(f"📁 {cat}", callback_data=f"cat_{cat}")] for cat in CATEGORIAS.keys()]
        teclado.append([InlineKeyboardButton("🔙 Volver al Menú", callback_data='volver_menu')])
        await query.edit_message_text("Selecciona la categoría del producto:", reply_markup=InlineKeyboardMarkup(teclado))
        return ELEGIR_CATEGORIA

    elif query.data == 'menu_buscar':
        teclado = [[InlineKeyboardButton("🔙 Volver al Menú", callback_data='volver_menu')]]
        await query.edit_message_text("🔍 Escribe el NOMBRE o CÓDIGO del producto:", reply_markup=InlineKeyboardMarkup(teclado))
        return BUSCAR_ITEM
        
    elif query.data == 'menu_reporte':
        await generar_reporte_excel(update, context)
        return MENU_PRINCIPAL

# ==========================================
# 4. EXPORTACIÓN A EXCEL (Para la Jefa)
# ==========================================
async def generar_reporte_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("⏳ Generando reporte estructurado de base de datos...")
    
    try:
        # Extraemos toda la tabla en 1 segundo
        respuesta = supabase.table("inventario_bodega").select("*").execute()
        datos_crudos = respuesta.data
        
        if not datos_crudos:
            await query.message.reply_text("❌ La bodega está vacía.")
            return await mostrar_menu(update, context)

        # "Aplanamos" el JSON mágico para que en Excel sean columnas normales
        filas_limpias = []
        for item in datos_crudos:
            fila = item.copy()
            extras = fila.pop('atributos_extra', {})
            if isinstance(extras, dict):
                fila.update(extras) # Mezcla el JSON con los datos fijos
            
            # Limpiamos columnas de sistema que no importan en el reporte
            fila.pop('foto_id', None)
            fila.pop('id', None)
            filas_limpias.append(fila)

        # Magia de Pandas: Convertimos la lista a un DataFrame y luego a Excel en RAM
        df = pd.DataFrame(filas_limpias)
        archivo_excel = io.BytesIO()
        df.to_excel(archivo_excel, index=False, sheet_name="Inventario")
        archivo_excel.seek(0)
        
        fecha_str = datetime.now().strftime("%d-%m-%Y")
        await query.message.reply_document(document=archivo_excel, filename=f"Inventario_Bodega_{fecha_str}.xlsx", caption="📊 Reporte generado con éxito.")
    except Exception as e:
        await query.message.reply_text(f"❌ Error generando Excel: {e}")
        
    return await mostrar_menu(update, context)

# ==========================================
# 5. REGISTRO (CON JSONB MÁGICO)
# ==========================================
async def seleccionar_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'volver_menu': return await mostrar_menu(update, context)
    
    categoria = query.data.replace("cat_", "")
    context.user_data['categoria'] = categoria
    
    # Armamos la lista de preguntas: Base + Específicas
    preguntas = CAMPOS_BASE + CATEGORIAS[categoria]
    context.user_data.update({'preguntas': preguntas, 'respuestas': {}, 'idx': 0})
    
    await query.edit_message_text(f"📝 *Categoría {categoria}*\nIntroduce: *{preguntas[0]}*", parse_mode='Markdown')
    return RECOLECTAR_DATOS

async def recolectar_datos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    preguntas = context.user_data['preguntas']
    idx = context.user_data['idx']
    col = preguntas[idx]
    
    if col.upper() == "FOTO":
        if not update.message.photo:
            await update.message.reply_text("⚠️ Envía una FOTO de Telegram.")
            return RECOLECTAR_DATOS
        context.user_data['respuestas'][col] = update.message.photo[-1].file_id
    else:
        context.user_data['respuestas'][col] = update.message.text

    if idx + 1 < len(preguntas):
        context.user_data['idx'] += 1
        await update.message.reply_text(f"Siguiente: *{preguntas[idx+1]}*", parse_mode='Markdown')
        return RECOLECTAR_DATOS
    else:
        await update.message.reply_text("⏳ Escribiendo en base de datos SQL...")
        r = context.user_data['respuestas']
        
        # Separar datos base de datos dinámicos (JSON)
        atributos_extra = {}
        for clave, valor in r.items():
            if clave not in CAMPOS_BASE:
                atributos_extra[clave] = valor
                
        # Preparar payload para Supabase
        user = update.message.from_user
        nombre_real = f"@{user.username}" if user.username else user.first_name
        
        payload = {
            "codigo": r.get("Codigo", f"S/C-{uuid.uuid4().hex[:4]}"),
            "producto": r.get("Producto", "N/A"),
            "categoria": context.user_data['categoria'],
            "cantidad": int(r.get("Cantidad", 0)) if r.get("Cantidad", "0").isdigit() else 0,
            "zona": r.get("Zona", "General"),
            "foto_id": r.get("Foto", None),
            "usuario_registro": nombre_real,
            "atributos_extra": atributos_extra
        }
        
        try:
            supabase.table("inventario_bodega").insert(payload).execute()
            await update.message.reply_text(f"✅ ¡Producto guardado a nivel de milisegundos!")
        except Exception as e:
            await update.message.reply_text(f"❌ Error de BD: Probablemente el código ya existe. {e}")
            
        return await mostrar_menu(update, context)

# ==========================================
# 6. BÚSQUEDA Y ACTUALIZACIÓN
# ==========================================
async def buscar_item_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    termino = update.message.text
    
    try:
        # Búsqueda difusa en SQL usando ilike (Ignora mayúsculas/minúsculas)
        res = supabase.table("inventario_bodega").select("*").or_(f"producto.ilike.%{termino}%,codigo.ilike.%{termino}%").execute()
        resultados = res.data
        
        if not resultados:
            teclado = [[InlineKeyboardButton("🔙 Volver al Menú", callback_data='volver_menu')]]
            await update.message.reply_text(f"❌ No encontré '{termino}'.", reply_markup=InlineKeyboardMarkup(teclado))
            return BUSCAR_ITEM

        for fila in resultados:
            info = f"📦 *{fila['producto']}*\n"
            info += f"• *Código:* {fila['codigo']}\n"
            info += f"• *Categoría:* {fila['categoria']}\n"
            info += f"• *Cantidad:* {fila['cantidad']}\n"
            info += f"• *Zona:* {fila['zona']}\n"
            
            # Desempaquetamos los atributos dinámicos
            if fila['atributos_extra']:
                for k, v in fila['atributos_extra'].items():
                    info += f"• *{k}:* {v}\n"
                    
            teclado = [
                [InlineKeyboardButton("➕ Ingresar Stock", callback_data=f"mod|add|{fila['id']}")],
                [InlineKeyboardButton("➖ Retirar Stock", callback_data=f"mod|sub|{fila['id']}")],
                [InlineKeyboardButton("🔙 Volver al Menú", callback_data='volver_menu')]
            ]
            
            foto_id = fila['foto_id']
            if foto_id and len(foto_id) > 10: # Validación simple de file_id
                try: await update.message.reply_photo(photo=foto_id, caption=info, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
                except: await update.message.reply_text(info + "\n*(Foto caducada en Telegram)*", reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
            else:
                await update.message.reply_text(info, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')

    except Exception as e:
        await update.message.reply_text(f"❌ Error buscando: {e}")
        
    return BUSCAR_ITEM 

async def preparar_modificacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'volver_menu': return await mostrar_menu(update, context)
    
    datos = query.data.split('|')
    context.user_data['mod_data'] = {'accion': datos[1], 'id_fila': datos[2]}
    
    accion_texto = "INGRESAR" if datos[1] == 'add' else "RETIRAR"
    teclado = [[InlineKeyboardButton("🔙 Cancelar", callback_data='volver_menu')]]
    
    if query.message.photo: await query.edit_message_caption(caption=f"⚙️ Vas a *{accion_texto}* stock.\nEscribe el número de unidades:", reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
    else: await query.edit_message_text(f"⚙️ Vas a *{accion_texto}* stock.\nEscribe el número de unidades:", reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
    
    return ESPERAR_CANTIDAD

async def procesar_modificacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isdigit():
        await update.message.reply_text("⚠️ Escribe un número válido.")
        return ESPERAR_CANTIDAD
        
    cant_mod = int(update.message.text)
    datos = context.user_data['mod_data']
    id_fila = datos['id_fila']
    
    try:
        # Obtenemos valor actual
        res = supabase.table("inventario_bodega").select("cantidad, producto").eq("id", id_fila).execute()
        valor_actual = res.data[0]['cantidad']
        nombre_prod = res.data[0]['producto']
        
        nuevo_valor = valor_actual + cant_mod if datos['accion'] == 'add' else valor_actual - cant_mod
        if nuevo_valor < 0: nuevo_valor = 0 
        
        # Actualizamos en Supabase (En milisegundos)
        supabase.table("inventario_bodega").update({
            "cantidad": nuevo_valor,
            "ultima_modificacion": "now()"
        }).eq("id", id_fila).execute()
            
        await update.message.reply_text(f"✅ ¡Stock de *{nombre_prod}* actualizado!\nDe {valor_actual} pasó a *{nuevo_valor}*.", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error actualizando BD: {e}")
        
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
            ELEGIR_CATEGORIA: [CallbackQueryHandler(seleccionar_categoria)],
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