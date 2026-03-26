import os
import json
import gspread
from google.oauth2.service_account import Credentials
from telegram.ext import Application, CommandHandler
from flask import Flask
import threading

# --- 1. CONFIGURACIÓN DEL SERVIDOR WEB (Para engañar a Render) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot de Inventario Activo y Corriendo."

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- 2. CONFIGURACIÓN SEGURA DE CREDENCIALES ---
# Render leerá el JSON desde una variable de entorno, no desde un archivo físico
google_credentials_json = os.environ.get("GOOGLE_CREDENTIALS")

if google_credentials_json:
    creds_dict = json.loads(google_credentials_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ])
    cliente_gspread = gspread.authorize(creds)
    # hoja_inventario = cliente_gspread.open("TuHoja").sheet1 

# --- 3. LÓGICA DEL BOT ---
# (Aquí van tus funciones async def start, etc.)

def main():
    # El token también debe ser secreto
    TOKEN = os.environ.get("TELEGRAM_TOKEN") 
    app_bot = Application.builder().token(TOKEN).build()
    
    # app_bot.add_handler(CommandHandler("start", start))
    
    # Iniciar Flask en un hilo separado
    threading.Thread(target=run_web_server).start()
    
    print("Iniciando Bot...")
    app_bot.run_polling()

if __name__ == "__main__":
    main()