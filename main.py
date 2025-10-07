import asyncio
import sqlite3
import logging
import re
import requests
import os
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Token do bot (será configurado no Render)
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_TOKEN_HERE')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DexToolsScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
        })
        
    def extract_dextools_info(self, url):
        try:
            pattern = r'dextools\.io/app/[^/]+/([^/]+)/pair-explorer/([a-fA-F0-9x]+)'
            match = re.search(pattern, url)
            if match:
                return match.group(1), match.group(2)
            return None, None
        except Exception as e:
            logger.error(f"Erro ao extrair info: {e}")
            return None, None
    
    def get_coin_data(self, chain, pair_address):
        try:
            url = f"https://www.dextools.io/app/en/{chain}/pair-explorer/{pair_address}"
            response = self.session.get(url, timeout=20)
            
            if response.status_code != 200:
                return {'success': False, 'error': f'HTTP {response.status_code}'}
            
            content = response.text
            
            # Extrair nome da moeda
            name_patterns = [
                r'"symbol":"([^"]+)"',
                r'<title>([A-Z]{2,10})[^<]*</title>'
            ]
            
            coin_name = "Unknown"
            for pattern in name_patterns:
                match = re.search(pattern, content)
                if match:
                    coin_name = match.group(1)
                    break
            
            # Extrair preço
            price_patterns = [
                r'"price":"([0-9.e-]+)"',
                r'price[^>]*>\$?([0-9.e-]+)<'
            ]
            
            current_price = 0
            for pattern in price_patterns:
                match = re.search(pattern, content)
                if match:
                    try:
                        current_price = float(match.group(1))
                        break
                    except:
                        continue
            
            # Extrair mudança 24h
            change_patterns = [
                r'"price24h":[^}]*"percent":([^,}]+)',
                r'24h[^>]*>([+-]?[0-9.]+)%'
            ]
            
            change_24h = 0
            for pattern in change_patterns:
                match = re.search(pattern, content)
                if match:
                    try:
                        change_24h = float(match.group(1))
                        break
                    except:
                        continue
            
            return {
                'success': True,
                'name': coin_name,
                'price': current_price,
                'change_24h': change_24h
            }
            
        except Exception as e:
            logger.error(f"Erro no scraping: {e}")
            return {
                'success': False,
                'error': str(e)
            }

class CryptoMonitorBot:
    def __init__(self, token):
        self.token = token
        self.application = Application.builder().token(token).build()
        self.scheduler = AsyncIOScheduler()
        self.db_path = "crypto_monitor.db"
        self.scraper = DexToolsScraper()
        self.init_database()
        self.setup_handlers()
        
    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS monitored_coins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                coin_name TEXT,
                pair_address TEXT,
                chain TEXT,
                target_percentage REAL,
                current_price REAL,
                last_percentage_change REAL,
                is_active BOOLEAN DEFAULT 1,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                coin_name TEXT,
                percentage_change REAL,
                price REAL,
                alert_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        
    def get_db_connection(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)
        
    def setup_handlers(self):
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("addcoin", self.add_coin_command))
        self.application.add_handler(CommandHandler("listcoins", self.list_coins_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
    async def start_command(self, update: Update, context):
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO users (user_id, username) VALUES (?, ?)', 
                      (user_id, username))
        conn.commit()
        conn.close()
        
        welcome_text = """
🚀 **Crypto Monitor Bot Online!**

Bot hospedado 24/7 no Render! 

**Comandos:**
/addcoin - Adicionar moeda
/listcoins - Ver suas moedas  
/status - Status do sistema
/help - Ajuda completa

Digite /addcoin para começar! 🪙
        """
        
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
    
    async def help_command(self, update: Update, context):
        help_text = """
📖 **Manual do Bot**

**Como usar:**
1. /addcoin - Cole link do DexTools
2. Digite percentual (ex: +15 ou -10)
3. Receba alertas automáticos!

**Exemplo completo:**
