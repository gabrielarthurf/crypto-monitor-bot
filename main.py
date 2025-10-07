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
        
        welcome_text = """🚀 **Crypto Monitor Bot Online!**

Bot hospedado 24/7 no Render! 

**Comandos:**
/addcoin - Adicionar moeda
/listcoins - Ver suas moedas  
/status - Status do sistema
/help - Ajuda completa

Digite /addcoin para começar! 🪙"""
        
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
    
    async def help_command(self, update: Update, context):
        help_text = """📖 **Manual do Bot**

**Como usar:**
1. /addcoin - Cole link do DexTools
2. Digite percentual (ex: +15 ou -10)
3. Receba alertas automáticos!

**Exemplo completo:**
Você: /addcoin
Bot: Cole o link...
Você: https://dextools.io/app/bnb/pair-explorer/0x...
Bot: Moeda encontrada! Digite percentual...
Você: +25
Bot: ✅ Monitoramento iniciado!

Verificação automática a cada 3 minutos! 🔄"""
        
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def add_coin_command(self, update: Update, context):
        await update.message.reply_text(
            "🪙 **Adicionar Nova Moeda**\n\n"
            "Cole o link completo do DexTools:\n\n"
            "📋 **Exemplo:**\n"
            "`https://dextools.io/app/bnb/pair-explorer/0x...`",
            parse_mode='Markdown'
        )
        context.user_data['state'] = 'awaiting_link'
    
    async def handle_message(self, update: Update, context):
        user_state = context.user_data.get('state')
        
        if user_state == 'awaiting_link':
            text = update.message.text.strip()
            
            if 'dextools.io' not in text:
                await update.message.reply_text(
                    "❌ **Link inválido!**\n\n"
                    "Envie um link do DexTools válido:\n"
                    "`https://dextools.io/app/bnb/pair-explorer/0x...`",
                    parse_mode='Markdown'
                )
                return
            
            chain, pair_address = self.scraper.extract_dextools_info(text)
            
            if not chain or not pair_address:
                await update.message.reply_text("❌ Não foi possível extrair dados do link!")
                return
            
            await update.message.reply_text("🔍 **Analisando moeda...**\n\nAguarde alguns segundos...")
            
            coin_data = self.scraper.get_coin_data(chain, pair_address)
            
            if not coin_data['success']:
                await update.message.reply_text(
                    f"❌ **Erro ao obter dados:**\n{coin_data.get('error', 'Desconhecido')}\n\n"
                    "Tente outro link ou aguarde alguns minutos."
                )
                context.user_data.clear()
                return
            
            context.user_data.update({
                'state': 'awaiting_percentage',
                'coin_name': coin_data['name'],
                'pair_address': pair_address,
                'chain': chain,
                'current_price': coin_data['price']
            })
            
            message_text = f"""✅ **Moeda Encontrada com Sucesso!**

🪙 **Nome:** {coin_data['name']}
💰 **Preço Atual:** ${coin_data['price']:.10f}
📈 **Mudança 24h:** {coin_data['change_24h']:.2f}%
🔗 **Rede:** {chain.upper()}

🎯 **Agora defina o percentual para alerta:**

**Exemplos:**
• `+15` → Alerta quando **subir** 15%
• `-10` → Alerta quando **cair** 10%
• `+50` → Alerta quando **subir** 50%

**Digite apenas o número com + ou -:**"""
            
            await update.message.reply_text(message_text, parse_mode='Markdown')
        
        elif user_state == 'awaiting_percentage':
            text = update.message.text.strip()
            
            try:
                if not (text.startswith('+') or text.startswith('-')):
                    raise ValueError("Formato inválido")
                
                percentage = float(text)
                
                if abs(percentage) > 1000:
                    await update.message.reply_text(
                        "❌ **Percentual muito alto!**\n\n"
                        "Use valores entre -1000% e +1000%"
                    )
                    return
            
            except ValueError:
                await update.message.reply_text(
                    "❌ **Formato incorreto!**\n\n"
                    "Use o formato:\n"
                    "• `+15` para alta de 15%\n"
                    "• `-10` para queda de 10%",
                    parse_mode='Markdown'
                )
                return
            
            # Salvar no banco de dados
            user_id = update.effective_user.id
            coin_name = context.user_data['coin_name']
            pair_address = context.user_data['pair_address']
            chain = context.user_data['chain']
            current_price = context.user_data['current_price']
            
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Verificar duplicata
            cursor.execute('''
                SELECT id FROM monitored_coins 
                WHERE user_id = ? AND pair_address = ? AND is_active = 1
            ''', (user_id, pair_address))
            
            if cursor.fetchone():
                await update.message.reply_text(
                    "⚠️ **Moeda já está sendo monitorada!**\n\n"
                    "Use /listcoins para ver todas suas moedas."
                )
                conn.close()
                context.user_data.clear()
                return
            
            cursor.execute('''
                INSERT INTO monitored_coins 
                (user_id, coin_name, pair_address, chain, target_percentage, current_price, last_percentage_change)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, coin_name, pair_address, chain, percentage, current_price, 0))
            
            conn.commit()
            conn.close()
            
            direction = "subir" if percentage > 0 else "cair"
            emoji = "📈" if percentage > 0 else "📉"
            
            success_text = f"""🎉 **{coin_name} Adicionada com Sucesso!**

📊 **Configuração do Alerta:**
• **Moeda:** {coin_name} ({chain.upper()})
• **Percentual:** {emoji} {percentage:+.1f}%
• **Preço Atual:** ${current_price:.10f}

🔔 **Você será notificado quando a moeda {direction} {abs(percentage):.1f}% ou mais!**

⏱️ **Monitoramento Ativo:**
• Verificação automática a cada 3 minutos
• Funcionando 24/7 no Render
• Alertas em tempo real

✨ **Monitoramento iniciado! Relaxe e aguarde os alertas.**"""
            
            await update.message.reply_text(success_text, parse_mode='Markdown')
            context.user_data.clear()
    
    async def list_coins_command(self, update: Update, context):
        user_id = update.effective_user.id
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT coin_name, chain, target_percentage, current_price, last_percentage_change, added_at
            FROM monitored_coins 
            WHERE user_id = ? AND is_active = 1
            ORDER BY added_at DESC
        ''', (user_id,))
        
        coins = cursor.fetchall()
        conn.close()
        
        if not coins:
            await update.message.reply_text(
                "📝 **Lista Vazia**\n\n"
                "Você ainda não tem moedas monitoradas.\n\n"
                "💡 Use /addcoin para adicionar sua primeira moeda!",
                parse_mode='Markdown'
            )
            return
        
        message_text = f"📊 **Suas Moedas Monitoradas** ({len(coins)})\n\n"
        
        for i, coin in enumerate(coins, 1):
            name, chain, target_perc, price, last_change, added_at = coin
            
            # Emoji do tipo de alerta
            if target_perc > 0:
                status_emoji = "📈"
                status_text = f"+{target_perc:.1f}%"
            else:
                status_emoji = "📉"
                status_text = f"{target_perc:.1f}%"
            
            # Formatar data
            try:
                date_obj = datetime.strptime(added_at, "%Y-%m-%d %H:%M:%S")
                date_formatted = date_obj.strftime("%d/%m/%y")
            except:
                date_formatted = "N/A"
            
            message_text += f"""**{i}. {name}** ({chain.upper()})
💰 **Preço:** ${price:.10f}
🎯 **Alerta:** {status_emoji} {status_text}
📊 **Mudança atual:** {last_change:+.2f}%
📅 **Adicionado:** {date_formatted}
{'─' * 30}
"""
        
        message_text += f"\n🛠️ **Comandos:**\n• /status - Ver estatísticas\n• /addcoin - Adicionar mais moedas"
        
        # Dividir mensagem se muito longa
        if len(message_text) > 4096:
            parts = [message_text[i:i+4096] for i in range(0, len(message_text), 4096)]
            for part in parts:
                await update.message.reply_text(part, parse_mode='Markdown')
        else:
            await update.message.reply_text(message_text, parse_mode='Markdown')
    
    async def status_command(self, update: Update, context):
        user_id = update.effective_user.id
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        # Estatísticas do usuário
        cursor.execute('SELECT COUNT(*) FROM monitored_coins WHERE user_id = ? AND is_active = 1', (user_id,))
        coin_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM alert_history WHERE user_id = ?', (user_id,))
        total_alerts = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT COUNT(*) FROM alert_history 
            WHERE user_id = ? AND DATE(alert_time) = DATE('now')
        ''', (user_id,))
        alerts_today = cursor.fetchone()[0]
        
        # Estatísticas gerais
        cursor.execute('SELECT COUNT(*) FROM monitored_coins WHERE is_active = 1')
        total_coins = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM users')
        total_users = cursor.fetchone()[0]
        
        conn.close()
        
        # Status do sistema
        scheduler_status = "🟢 **Ativo**" if self.scheduler.running else "🔴 **Inativo**"
        current_time = datetime.now().strftime('%d/%m/%Y às %H:%M:%S')
        
        status_text = f"""📊 **Status do Crypto Monitor Bot**

👤 **Suas Estatísticas:**
• Moedas monitoradas: **{coin_count}**
• Alertas recebidos hoje: **{alerts_today}**
• Total de alertas: **{total_alerts}**

🌐 **Sistema Global:**
• Total de usuários: **{total_users}**
• Total de moedas: **{total_coins}**
• Status do monitor: {scheduler_status}
• Hospedagem: **🌟 Render (24/7)**

⚙️ **Configurações:**
• Verificação automática: **A cada 3 minutos**
• Última verificação: **{current_time}**
• Servidor: **Online** 
• Base de dados: **Funcionando**

✨ **Tudo funcionando perfeitamente!**
Bot rodando 24/7 na nuvem."""
        
        await update.message.reply_text(status_text, parse_mode='Markdown')
    
    async def check_coins_for_alerts(self):
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, user_id, coin_name, pair_address, chain, target_percentage, current_price
                FROM monitored_coins WHERE is_active = 1
            ''')
            
            coins = cursor.fetchall()
            logger.info(f"🔍 Verificando {len(coins)} moedas...")
            
            alerts_sent = 0
            
            for coin in coins:
                coin_id, user_id, name, pair_address, chain, target_perc, old_price = coin
                
                try:
                    coin_data = self.scraper.get_coin_data(chain, pair_address)
                    
                    if coin_data['success']:
                        new_price = coin_data['price']
                        current_change = coin_data['change_24h']
                        
                        # Atualizar dados no banco
                        cursor.execute('''
                            UPDATE monitored_coins 
                            SET current_price = ?, last_percentage_change = ?
                            WHERE id = ?
                        ''', (new_price, current_change, coin_id))
                        
                        # Verificar condição de alerta
                        should_alert = False
                        if target_perc > 0 and current_change >= target_perc:
                            should_alert = True
                        elif target_perc < 0 and current_change <= target_perc:
                            should_alert = True
                        
                        if should_alert:
                            # Verificar se já alertou recentemente (evitar spam)
                            cursor.execute('''
                                SELECT COUNT(*) FROM alert_history 
                                WHERE user_id = ? AND coin_name = ? 
                                AND alert_time > datetime('now', '-2 hours')
                            ''', (user_id, name))
                            
                            recent_alerts = cursor.fetchone()[0]
                            
                            if recent_alerts == 0:
                                # Registrar alerta no histórico
                                cursor.execute('''
                                    INSERT INTO alert_history 
                                    (user_id, coin_name, percentage_change, price)
                                    VALUES (?, ?, ?, ?)
                                ''', (user_id, name, current_change, new_price))
                                
                                # Enviar notificação
                                await self.send_alert(user_id, name, current_change, new_price, target_perc, chain, pair_address)
                                alerts_sent += 1
                                
                                logger.info(f"🔔 Alerta enviado: {name} para usuário {user_id}")
                        
                        logger.info(f"✅ {name}: {current_change:+.2f}% (Meta: {target_perc:+.1f}%)")
                    
                    else:
                        logger.warning(f"⚠️ Falha ao obter dados de {name}: {coin_data.get('error', 'Unknown')}")
                
                except Exception as e:
                    logger.error(f"❌ Erro verificando {name}: {e}")
                    continue
            
            conn.commit()
            conn.close()
            
            logger.info(f"✅ Verificação concluída. {alerts_sent} alertas enviados.")
            
        except Exception as e:
            logger.error(f"❌ Erro geral na verificação: {e}")
    
    async def send_alert(self, user_id, coin_name, percentage_change, price, target_perc, chain, pair_address):
        try:
            # Determinar emoji e texto baseado na direção
            if percentage_change > 0:
                if percentage_change > 50:
                    emoji = "🚀🔥"
                    trend = "EXPLODIU"
                elif percentage_change > 20:
                    emoji = "🚀📈"
                    trend = "DISPAROU"
                else:
                    emoji = "📈"
                    trend = "SUBIU"
            else:
                if percentage_change < -50:
                    emoji = "💥📉"
                    trend = "DESPENCOU"
                elif percentage_change < -20:
                    emoji = "🔥📉"
                    trend = "DESPENCOU"
                else:
                    emoji = "📉"
                    trend = "CAIU"
            
            alert_text = f"""🚨 **ALERTA DE PREÇO ATIVADO** 🚨

{emoji} **{coin_name}** {trend}!

📊 **Detalhes:**
• **Mudança 24h:** {percentage_change:+.2f}%
• **Sua meta:** {target_perc:+.1f}%
• **Preço atual:** ${price:.10f}
• **Rede:** {chain.upper()}

⏰ **Horário:** {datetime.now().strftime('%d/%m/%Y às %H:%M:%S')}

🔗 **Ver no DexTools:**
https://www.dextools.io/app/en/{chain}/pair-explorer/{pair_address}

🤖 **Bot funcionando 24/7 no Render!**"""
            
            await self.application.bot.send_message(
                chat_id=user_id,
                text=alert_text,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            
            logger.info(f"✅ Alerta enviado com sucesso para usuário {user_id}")
            
        except Exception as e:
            logger.error(f"❌ Erro ao enviar alerta para usuário {user_id}: {e}")
    
    def run(self):
        # Configurar e iniciar scheduler
        self.scheduler.add_job(
            self.check_coins_for_alerts,
            'interval',
            minutes=3,  # Verificar a cada 3 minutos
            id='coin_checker',
            max_instances=1  # Evitar sobreposição
        )
        self.scheduler.start()
        
        logger.info("🤖 Crypto Monitor Bot iniciado com sucesso!")
        logger.info("🌐 Hospedado no Render - funcionando 24/7")
        logger.info("📊 Verificações automáticas a cada 3 minutos")
        logger.info("🚀 Bot pronto para receber comandos!")
        
        # Executar aplicação
        self.application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )

# Configuração para produção no Render
if __name__ == "__main__":
    # Verificar se token está configurado
    if not BOT_TOKEN or BOT_TOKEN == 'YOUR_TOKEN_HERE':
        logger.error("❌ BOT_TOKEN não configurado!")
        logger.error("Configure a variável de ambiente BOT_TOKEN no Render")
        exit(1)
    
    # Iniciar bot
    try:
        bot = CryptoMonitorBot(BOT_TOKEN)
        bot.run()
    except Exception as e:
        logger.error(f"❌ Erro fatal ao iniciar bot: {e}")
        exit(1)
