import os, threading, uuid, io
from datetime import datetime
import pandas as pd

import matplotlib
matplotlib.use('Agg') # Obligatorio para generar gráficos en servidores como Render
import matplotlib.pyplot as plt

from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    ConversationHandler, ContextTypes, filters
)
from flask import Flask

# ==========================================
# 1. CONFIGURACIÓN SUPABASE
# ==========================================
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key) if url and key else None

app_flask = Flask(__name__)
MENU_PRINCIPAL, ELEGIR_CATEGORIA, RECOLECTAR_DATOS, BUSCAR_ITEM, ESPERAR_CANTIDAD, CREAR_CAT_NOMBRE, CREAR_CAT_CAMPOS = range(7)

CAMPOS_BASE = ["Codigo", "Producto", "Cantidad", "Zona", "Foto"]

@app_flask.route('/')
def home(): return "✅ ERP Bodega V7 (Gráficos Pastel & Anti-Bugs) Activo"

def run_web_server():
    app_flask.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# ==========================================
# 2. INTERFAZ Y NAVEGACIÓN BLINDADA
# ==========================================
async def mostrar_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name or "Usuario"
    texto = f"👋 Hola {user_name}, ¿qué deseas hacer hoy?"
    
    teclado = [
        [InlineKeyboardButton("📦 Registrar Ingreso", callback_data='menu_registrar')],
        [InlineKeyboardButton("🔍 Buscar / Gestionar Stock", callback_data='menu_buscar')],
        [InlineKeyboardButton("📊 Ver Gráfico de Distribución", callback_data='menu_grafico')],
        [InlineKeyboardButton("📥 Exportar a Excel", callback_data='menu_excel')]
    ]
    markup = InlineKeyboardMarkup(teclado)

    if update.message:
        await update.message.reply_text(texto, reply_markup=markup)
    else:
        query = update.callback_query
        try:
            # Intenta editar el mensaje de texto
            await query.edit_message_text(texto, reply_markup=markup)
        except Exception:
            # SI FALLA (porque el botón estaba en una foto), borra la foto y manda texto nuevo.
            await query.message.delete()
            await query.message.reply_text(texto, reply_markup=markup)
            
    return MENU_PRINCIPAL

async def manejador_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'volver_menu': return await mostrar_menu(update, context)
    
    if query.data == 'menu_registrar':
        res = supabase.table("config_categorias").select("nombre").execute()
        categorias_bd = [item['nombre'] for item in res.data]
        
        teclado = [[InlineKeyboardButton(f"📁 {cat}", callback_data=f"cat_{cat}")] for cat in categorias_bd]
        teclado.append([InlineKeyboardButton("✨ Crear Categoría Nueva", callback_data='crear_categoria')])
        teclado.append([InlineKeyboardButton("🔙 Volver", callback_data='volver_menu')])
        
        await query.edit_message_text("Selecciona la categoría a ingresar:", reply_markup=InlineKeyboardMarkup(teclado))
        return ELEGIR_CATEGORIA

    elif query.data == 'menu_buscar':
        teclado = [[InlineKeyboardButton("🔙 Volver al Menú", callback_data='volver_menu')]]
        await query.edit_message_text("🔍 Escribe el NOMBRE o CÓDIGO del producto:", reply_markup=InlineKeyboardMarkup(teclado))
        return BUSCAR_ITEM
        
    elif query.data == 'menu_grafico':
        return await generar_grafico_pastel(update, context)
        
    elif query.data == 'menu_excel':
        return await generar_excel(update, context)

# ==========================================
# 3. EL CONSTRUCTOR DE CATEGORÍAS
# ==========================================
async def iniciar_creacion_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    teclado = [[InlineKeyboardButton("🔙 Cancelar", callback_data='volver_menu')]]
    await query.edit_message_text("✨ *Nueva Categoría*\n\nEscribe el nombre de la categoría (Ej. Cintas, Lentes, Cables):", reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
    return CREAR_CAT_NOMBRE

async def guardar_nombre_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nombre = update.message.text.strip().capitalize()
    context.user_data['nueva_cat_nombre'] = nombre
    await update.message.reply_text(f"Ahora, escribe los *atributos específicos* para *{nombre}*, separados por comas.\nEjemplo: `Medida, Color, Empaque`", parse_mode='Markdown')
    return CREAR_CAT_CAMPOS

async def guardar_campos_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lista_campos = [campo.strip() for campo in update.message.text.split(',')]
    nombre_cat = context.user_data['nueva_cat_nombre']
    
    try:
        supabase.table("config_categorias").insert({"nombre": nombre_cat, "campos": lista_campos}).execute()
        await update.message.reply_text(f"✅ ¡Categoría *{nombre_cat}* lista para usarse!", parse_mode='Markdown')
    except Exception:
        await update.message.reply_text("❌ Error: La categoría probablemente ya existe.")
    return await mostrar_menu(update, context)

# ==========================================
# 4. GRÁFICOS Y REPORTES (SEPARADOS)
# ==========================================
async def generar_grafico_pastel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("⏳ Dibujando gráfica de distribución...")
    
    try:
        res = supabase.table("inventario_bodega").select("categoria").execute()
        df = pd.DataFrame(res.data)
        
        if df.empty:
            await query.message.reply_text("❌ No hay productos para graficar.")
            return await mostrar_menu(update, context)

        conteo = df['categoria'].value_counts()
        
        plt.figure(figsize=(8, 8))
        # Generación de Gráfico de Pastel
        plt.pie(conteo, labels=conteo.index, autopct='%1.1f%%', startangle=140, colors=plt.cm.tab20.colors, wedgeprops={'edgecolor': 'black'})
        plt.title("Distribución del Inventario por Categorías", fontsize=14, fontweight='bold')
        
        img_buf = io.BytesIO()
        plt.savefig(img_buf, format='png', bbox_inches='tight')
        img_buf.seek(0)
        
        await query.message.delete() # Borramos el mensaje de "Dibujando..."
        await query.message.reply_photo(photo=img_buf, caption="📊 *Gráfico de Distribución*\nUsa /menu para volver.", parse_mode='Markdown')
        plt.close() 
        
    except Exception as e:
        await query.message.reply_text(f"❌ Error generando gráfico: {e}")
        
    return MENU_PRINCIPAL

async def generar_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("⏳ Compilando datos en archivo Excel...")
    
    try:
        res = supabase.table("inventario_bodega").select("*").execute()
        if not res.data:
            await query.message.reply_text("❌ La bodega está vacía.")
            return await mostrar_menu(update, context)

        filas_limpias = []
        for item in res.data:
            fila = item.copy()
            extras = fila.pop('atributos_extra', {})
            if isinstance(extras, dict): fila.update(extras) 
            fila.pop('foto_id', None)
            fila.pop('id', None)
            filas_limpias.append(fila)

        df = pd.DataFrame(filas_limpias)
        fecha_str = datetime.now().strftime("%d-%m-%Y")
        archivo_excel = io.BytesIO()
        
        with pd.ExcelWriter(archivo_excel, engine='openpyxl') as writer:
            for cat in df['categoria'].unique():
                df_cat = df[df['categoria'] == cat].dropna(axis=1, how='all')
                df_cat.to_excel(writer, index=False, sheet_name=str(cat)[:31])

        archivo_excel.seek(0)
        await query.message.delete()
        await query.message.reply_document(document=archivo_excel, filename=f"Inventario_Bodega_{fecha_str}.xlsx", caption="📁 Reporte gerencial listo.\nUsa /menu para volver.")
        
    except Exception as e:
        await query.message.reply_text(f"❌ Error generando Excel: {e}")
        
    return MENU_PRINCIPAL

# ==========================================
# 5. REGISTRO (CON SUPABASE)
# ==========================================
async def seleccionar_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'crear_categoria': return await iniciar_creacion_categoria(update, context)
    if query.data == 'volver_menu': return await mostrar_menu(update, context)
    
    categoria = query.data.replace("cat_", "")
    context.user_data['categoria'] = categoria
    
    res = supabase.table("config_categorias").select("campos").eq("nombre", categoria).execute()
    campos_dinamicos = res.data[0]['campos'] if res.data else []
    
    preguntas = CAMPOS_BASE + campos_dinamicos
    context.user_data.update({'preguntas': preguntas, 'respuestas': {}, 'idx': 0})
    
    await query.edit_message_text(f"📝 *Categoría {categoria}*\nIntroduce: *{preguntas[0]}*", parse_mode='Markdown')
    return RECOLECTAR_DATOS

async def recolectar_datos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    preguntas = context.user_data['preguntas']
    idx = context.user_data['idx']
    col = preguntas[idx]
    
    if col.upper() == "FOTO":
        if not update.message.photo:
            await update.message.reply_text("⚠️ Envía una FOTO.")
            return RECOLECTAR_DATOS
        context.user_data['respuestas'][col] = update.message.photo[-1].file_id
    else:
        context.user_data['respuestas'][col] = update.message.text

    if idx + 1 < len(preguntas):
        context.user_data['idx'] += 1
        await update.message.reply_text(f"Siguiente: *{preguntas[idx+1]}*", parse_mode='Markdown')
        return RECOLECTAR_DATOS
    else:
        await update.message.reply_text("⏳ Guardando...")
        r = context.user_data['respuestas']
        atributos_extra = {k: v for k, v in r.items() if k not in CAMPOS_BASE}
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
            await update.message.reply_text("✅ Producto guardado.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error al guardar (¿El código ya existe?): {e}")
            
        return await mostrar_menu(update, context)

# ==========================================
# 6. BÚSQUEDA CORREGIDA (ANTI-CRASH)
# ==========================================
async def buscar_item_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    termino = update.message.text
    try:
        res = supabase.table("inventario_bodega").select("*").or_(f"producto.ilike.%{termino}%,codigo.ilike.%{termino}%").execute()
        if not res.data:
            await update.message.reply_text(f"❌ No encontré '{termino}'.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data='volver_menu')]]))
            return BUSCAR_ITEM

        for fila in res.data:
            info = f"📦 *{fila['producto']}*\n• *Código:* {fila['codigo']}\n• *Categoría:* {fila['categoria']}\n• *Cantidad:* {fila['cantidad']}\n• *Zona:* {fila['zona']}\n"
            if fila['atributos_extra']:
                for k, v in fila['atributos_extra'].items(): info += f"• *{k}:* {v}\n"
                    
            teclado = [
                [InlineKeyboardButton("➕ Ingresar", callback_data=f"mod|add|{fila['id']}"), InlineKeyboardButton("➖ Retirar", callback_data=f"mod|sub|{fila['id']}")],
            ]
            
            if fila['foto_id']:
                try: await update.message.reply_photo(photo=fila['foto_id'], caption=info, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
                except: await update.message.reply_text(info + "\n*(Foto no disponible)*", reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
            else: await update.message.reply_text(info, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
            
        # Único mensaje de texto al final con el botón de Volver (Soluciona el bug de la foto)
        final_teclado = [[InlineKeyboardButton("🔙 Volver al Menú", callback_data='volver_menu')]]
        await update.message.reply_text("✅ *Búsqueda completada.*\nPuedes escribir otro nombre para buscar de nuevo, o presionar Volver.", reply_markup=InlineKeyboardMarkup(final_teclado), parse_mode='Markdown')
        
    except Exception as e: 
        await update.message.reply_text(f"❌ Error: {e}")
    return BUSCAR_ITEM 

async def preparar_modificacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'volver_menu': return await mostrar_menu(update, context)
    context.user_data['mod_data'] = {'accion': query.data.split('|')[1], 'id_fila': query.data.split('|')[2]}
    txt = f"⚙️ *{'INGRESAR' if context.user_data['mod_data']['accion'] == 'add' else 'RETIRAR'}* stock.\nEscribe el número:"
    if query.message.photo: await query.edit_message_caption(caption=txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancelar", callback_data='volver_menu')]]), parse_mode='Markdown')
    else: await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancelar", callback_data='volver_menu')]]), parse_mode='Markdown')
    return ESPERAR_CANTIDAD

async def procesar_modificacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isdigit(): return ESPERAR_CANTIDAD
    datos = context.user_data['mod_data']
    try:
        res = supabase.table("inventario_bodega").select("cantidad, producto").eq("id", datos['id_fila']).execute()
        nuevo_valor = max(0, res.data[0]['cantidad'] + (int(update.message.text) if datos['accion'] == 'add' else -int(update.message.text)))
        supabase.table("inventario_bodega").update({"cantidad": nuevo_valor, "ultima_modificacion": "now()"}).eq("id", datos['id_fila']).execute()
        await update.message.reply_text(f"✅ ¡Stock de *{res.data[0]['producto']}* actualizado a *{nuevo_valor}*!", parse_mode='Markdown')
    except Exception as e: await update.message.reply_text(f"❌ Error: {e}")
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
            ],
            CREAR_CAT_NOMBRE: [
                CallbackQueryHandler(manejador_menu, pattern='^volver_menu$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_nombre_categoria)
            ],
            CREAR_CAT_CAMPOS: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_campos_categoria)]
        },
        fallbacks=[CommandHandler('menu', mostrar_menu)]
    )
    app.add_handler(conv)
    threading.Thread(target=run_web_server, daemon=True).start()
    app.run_polling()

if __name__ == "__main__": main()