import logging
import random
import sqlite3
import os
import time
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import pandas as pd
import asyncio  # Importa asyncio per operazioni asincrone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
)
from flask import Flask, request, Response # Importa Flask e i suoi componenti

# Configurazione del logger
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Recupera il token del bot e l'URL del servizio da variabili d'ambiente
# TELEGRAM_BOT_TOKEN deve essere impostato su Cloud Run
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    logger.error("La variabile d'ambiente TELEGRAM_BOT_TOKEN non è impostata. Il bot non può avviarsi.")
    exit(1)

# K_SERVICE_URL è una variabile d'ambiente fornita da Cloud Run con l'URL del servizio
# Useremo questo per costruire l'URL del webhook di Telegram.
CLOUD_RUN_URL = os.environ.get("K_SERVICE_URL")
if not CLOUD_RUN_URL:
    logger.error("La variabile d'ambiente K_SERVICE_URL non è impostata. Il bot non può impostare il webhook.")
    exit(1)

# L'URL completo per il webhook di Telegram deve includere il token per sicurezza e routing.
WEBHOOK_TELEGRAM_URL = f"{CLOUD_RUN_URL}/{TOKEN}"

# Inizializzazione dell'applicazione Flask
app = Flask(__name__)

# Inizializzazione dell'applicazione Telegram una volta all'avvio del contenitore
application = ApplicationBuilder().token(TOKEN).build()

# Costanti per la ConversationHandler
CHOOSE_CATEGORY, ASK_QUESTION = range(2)
user_data = {}

# Effetti visivi
CORRECT_EMOJIS = ["✅"]
WRONG_EMOJIS = ["❌"]
DELAY_BEFORE_NEXT_QUESTION = 1.5  # Secondi

# Database
def init_db():
    conn = sqlite3.connect('scores.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS scores
                 (user_id INT, username TEXT, category TEXT, score INT, 
                  total INT, timestamp DATETIME)''')
    conn.commit()
    conn.close()

# init_db() verrà chiamato nel blocco if __name__ == "__main__"

async def save_score(user_id: int, username: str, category: str, score: int, total: int):
    conn = sqlite3.connect('scores.db')
    c = conn.cursor()
    c.execute("INSERT INTO scores VALUES (?, ?, ?, ?, ?, datetime('now'))",
              (user_id, username, category, score, total))
    conn.commit()
    conn.close()

# Generazione immagine punteggio
# ASSICURATI che un font compatibile sia disponibile nel tuo container.
# Potrebbe essere necessario installare un pacchetto di font come `fonts-dejavu-core`
# nel tuo Dockerfile o fornire il file del font (es. copiandolo nella directory).
async def generate_score_image(user_data: dict, user_id: int):
    try:
        img = Image.new('RGB', (800, 400), color=(58, 95, 205))
        d = ImageDraw.Draw(img)
        
        # Carica il font. Se 'arial.ttf' non funziona, prova un font predefinito
        # o installane uno nel Dockerfile e usa il percorso completo (es. "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf").
        try:
            # Assicurati che questo percorso sia corretto nel tuo container
            font_path = os.path.join(os.getcwd(), "arial.ttf") # Se copi 'arial.ttf' nella root del progetto
            if not os.path.exists(font_path):
                 # Prova un percorso comune per font di sistema in Debian/Ubuntu
                 font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            font_large = ImageFont.truetype(font_path, 40)
            font_medium = ImageFont.truetype(font_path, 30)
        except IOError:
            logger.warning("Font non trovato, usando il font predefinito.")
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()

        d.text((50, 50), "Certificato Quiz Sanitario", fill=(255, 255, 255), font=font_large)
        
        lines = [
            f"Utente: {user_data.get('username', '')}",
            f"Categoria: {user_data.get('category', '').title()}",
            f"Punteggio: {user_data['score']}/{len(user_data['questions'])}",
            f"Data: {datetime.now().strftime('%d/%m/%Y')}"
        ]
        
        for i, line in enumerate(lines):
            d.text((50, 120 + i*50), line, fill=(255, 255, 255), font=font_medium)
        
        img_path = f"/tmp/temp_score_{user_id}.png" # Salva in /tmp per Cloud Run (filesystem scrivibile)
        img.save(img_path)
        return img_path
    except Exception as e:
        logger.error(f"Errore generazione immagine: {e}")
        return None

async def delete_previous_messages(context, chat_id, message_ids):
    """Elimina i messaggi precedenti"""
    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            # Ignora errori se il messaggio è già stato cancellato o non esiste
            if "message to delete not found" not in str(e):
                logger.error(f"Errore cancellazione messaggio {msg_id}: {e}")

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id] = {
        "score": 0, 
        "current": 0, 
        "questions": [], 
        "username": update.effective_user.username,
        "last_messages": []  # Per tenere traccia dei messaggi da eliminare
    }

    try:
        # Assicurati che quiz.xlsx sia nella directory di lavoro del container
        quiz_df = pd.read_excel("quiz.xlsx")
        categories = quiz_df["categoria"].dropna().unique().tolist()
        
        keyboard = [[InlineKeyboardButton(f"📚 {cat.title()}", callback_data=cat)] for cat in categories]
        reply_markup = InlineKeyboardMarkup(keyboard)

        sent_message = await update.message.reply_text(
            "👋 Benvenuto al *Quiz Bot!*\n\nScegli una categoria:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        user_data[user_id]["last_messages"].append(sent_message.message_id) # Aggiungi il messaggio al tracking
        return CHOOSE_CATEGORY
    except FileNotFoundError:
        logger.error("Il file 'quiz.xlsx' non è stato trovato!")
        await update.message.reply_text("⚠️ Il file dei quiz non è stato trovato. Contatta l'amministratore.")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Errore avvio bot: {e}")
        await update.message.reply_text("⚠️ Errore nel caricamento dei quiz. Riprova più tardi.")
        return ConversationHandler.END

async def choose_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        category = query.data
        user_id = query.from_user.id
        quiz_df = pd.read_excel("quiz.xlsx")
        
        questions = quiz_df[quiz_df["categoria"] == category].to_dict(orient="records")
        if not questions:
            await query.edit_message_text("⚠️ Nessuna domanda per questa categoria!")
            return ConversationHandler.END
            
        random.shuffle(questions)
        user_data[user_id].update({
            "questions": questions[:20],  # Limita a 20 domande
            "score": 0,
            "current": 0,
            "category": category,
            "last_messages": []  # Reset messaggi precedenti
        })

        await query.edit_message_text(
            f"📘 Categoria: *{category.title()}*\n\nRispondi alle domande:",
            parse_mode=ParseMode.MARKDOWN
        )
        return await ask_question(update, context)
    except Exception as e:
        logger.error(f"Errore scelta categoria: {e}")
        await query.edit_message_text("⚠️ Errore nel caricamento delle domande.")
        return ConversationHandler.END

async def ask_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query if hasattr(update, 'callback_query') else None
    user_id = update.effective_user.id
    
    if user_id not in user_data:
        if query:
            await query.edit_message_text("⚠️ Sessione scaduta. Usa /start")
        return ConversationHandler.END

    data = user_data[user_id]
    
    # Cancella i messaggi precedenti
    if data["last_messages"]:
        await delete_previous_messages(context, user_id, data["last_messages"])
        data["last_messages"] = []
    
    if data["current"] >= len(data["questions"]):
        score = data['score']
        total = len(data['questions'])
        
        await save_score(
            user_id=user_id,
            username=data['username'],
            category=data['category'],
            score=score,
            total=total
        )

        share_button = InlineKeyboardButton("🎯 Condividi punteggio", callback_data="share_options")
        msg = await context.bot.send_message(
            chat_id=user_id,
            text=f"🏁 *Quiz Completato!*\n\nPunteggio: *{score}/{total}*",
            reply_markup=InlineKeyboardMarkup([[share_button]]),
            parse_mode=ParseMode.MARKDOWN
        )
        data["last_messages"].append(msg.message_id)
        return ConversationHandler.END

    q = data["questions"][data["current"]]
    options = q["opzioni"].split(";") if isinstance(q["opzioni"], str) else []
    data["correct_answer"] = q["risposta"]
    
    # IMPORTANTE: Se usi immagini, devono essere presenti nel contenitore.
    # Assicurati che il tuo Dockerfile copi anche la cartella delle immagini.
    image_path = q["immagine"] if "immagine" in q and pd.notna(q["immagine"]) else None

    keyboard = [[InlineKeyboardButton(opt, callback_data=opt)] for opt in options]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Barra di progresso con emoji dinamica
    progress = "🟩" * data["current"] + "⬜" * (len(data["questions"]) - data["current"])
    question_text = (
        f"📊 Progresso: {progress}\n\n"
        f"❓ *Domanda {data['current'] + 1}/{len(data['questions'])}*\n\n"
        f"*{q['domanda']}*"
    )

    # Invia prima l'immagine se esiste
    if image_path and os.path.exists(image_path):
        try:
            with open(image_path, 'rb') as photo:
                img_msg = await context.bot.send_photo(
                    chat_id=user_id,
                    photo=InputFile(photo),
                    caption=" ", # Didascalia vuota o personalizzata
                    parse_mode=ParseMode.MARKDOWN
                )
                data["last_messages"].append(img_msg.message_id)
                await asyncio.sleep(0.5) # Piccolo delay per l'effetto visivo
        except Exception as e:
            logger.warning(f"Impossibile inviare immagine {image_path}: {e}")
    else:
        if image_path: # Se il percorso dell'immagine è definito ma il file non esiste
            logger.warning(f"Immagine non trovata o percorso errato: {image_path}")


    # Invia la domanda
    msg = await context.bot.send_message(
        chat_id=user_id,
        text=question_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    data["last_messages"].append(msg.message_id)
    
    return ASK_QUESTION

async def answer_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    answer = query.data
    data = user_data[user_id]

    # Effetto visivo con emoji casuale
    correct = answer == data["correct_answer"]
    emoji = random.choice(CORRECT_EMOJIS) if correct else random.choice(WRONG_EMOJIS)
    
    if correct:
        response = f"{emoji} *Corretto!*"
        data["score"] += 1
    else:
        response = f"{emoji} *Sbagliato!*\n\nLa risposta era: *{data['correct_answer']}*"

    # Invia il risultato come nuovo messaggio
    msg = await context.bot.send_message(
        chat_id=user_id,
        text=f"{emoji * 3}\n\n{response}",
        parse_mode=ParseMode.MARKDOWN
    )
    data["last_messages"].append(msg.message_id)

    # Delay prima della prossima domanda
    await asyncio.sleep(DELAY_BEFORE_NEXT_QUESTION) # Usa asyncio.sleep() per non bloccare il server
    
    data["current"] += 1
    return await ask_question(update, context)

async def share_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    buttons = [
        [InlineKeyboardButton("📤 Condividi come testo", callback_data="share_text")],
        [InlineKeyboardButton("🖼️ Condividi come immagine", callback_data="share_image")],
        [InlineKeyboardButton("🏆 Mostra classifica", callback_data="show_leaderboard")]
    ]
    
    # Edita il messaggio del pulsante per mostrare le nuove opzioni
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))

async def handle_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = user_data.get(user_id, {})
    
    if not data or 'questions' not in data or not data['questions']: # Aggiungi controllo per 'questions'
        await query.edit_message_text("⚠️ Completa prima un quiz con /start")
        return

    if query.data == "share_text":
        share_text = (
            f"🏥 *Risultato Quiz Sanitario*\n\n"
            f"👤 Utente: @{query.from_user.username}\n"
            f"📚 Categoria: {data['category'].title()}\n"
            f"🎯 Punteggio: {data['score']}/{len(data['questions'])}\n\n"
            f"Prova anche tu: @{context.bot.username}"
        )
        
        await context.bot.send_message(
            chat_id=user_id,
            text=share_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "Condividi in un gruppo", 
                    switch_inline_query=share_text.split('\n\n')[0] # Prende la prima riga come query inline
                )
            ]])
        )
    
    elif query.data == "share_image":
        img_path = await generate_score_image(data, user_id)
        if img_path:
            try:
                with open(img_path, 'rb') as img_file: # Assicurati di aprire il file
                    await context.bot.send_photo(
                        chat_id=user_id,
                        photo=InputFile(img_file),
                        caption=f"Condividi il tuo risultato! @{context.bot.username}",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton(
                                "Condividi", 
                                switch_inline_query="Guarda il mio certificato!"
                            )
                        ]])
                    )
            except Exception as e:
                logger.error(f"Errore nell'invio dell'immagine: {e}")
                await query.edit_message_text("⚠️ Errore nell'invio dell'immagine.")
            finally:
                if os.path.exists(img_path): # Assicurati di eliminare il file temporaneo
                    os.remove(img_path)
        else:
            await query.edit_message_text("⚠️ Errore nella generazione dell'immagine")
    
    elif query.data == "show_leaderboard":
        conn = sqlite3.connect('scores.db')
        c = conn.cursor()
        c.execute('''SELECT username, score, total FROM scores 
                     WHERE category = ? ORDER BY score DESC LIMIT 10''',
                    (data['category'],))
        top_scores = c.fetchall()
        conn.close()
        
        leaderboard = "🏆 *Classifica Top 10*\n\n"
        if not top_scores:
            leaderboard += "Nessun punteggio registrato per questa categoria."
        else:
            for i, (username, score, total) in enumerate(top_scores):
                leaderboard += f"{i+1}. @{username}: {score}/{total}\n"
        
        await query.edit_message_text(
            text=leaderboard,
            parse_mode=ParseMode.MARKDOWN
        )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_data:
        if "last_messages" in user_data[user_id]:
            await delete_previous_messages(context, user_id, user_data[user_id]["last_messages"])
        del user_data[user_id]
    await update.message.reply_text("❌ Quiz annullato.")
    return ConversationHandler.END

# Aggiungi gli handler all'applicazione Telegram una sola volta
application.add_handler(ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        CHOOSE_CATEGORY: [CallbackQueryHandler(choose_category)],
        ASK_QUESTION: [CallbackQueryHandler(answer_question)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
))
application.add_handler(CallbackQueryHandler(share_options, pattern="^share_options$"))
application.add_handler(CallbackQueryHandler(handle_share, pattern="^(share_text|share_image|show_leaderboard)$"))


# Endpoint Flask per il webhook di Telegram
# Cloud Run invierà le richieste POST a questo percorso.
@app.route(f"/{TOKEN}", methods=["POST"])
async def telegram_webhook():
    # Ottieni i dati JSON dalla richiesta POST
    req_body = request.get_json(force=True)
    
    # Crea un oggetto Update da Telegram e processalo con la tua applicazione bot
    update = Update.de_json(req_body, application.bot)
    
    # Processa l'update in modo asincrono
    await application.process_update(update)
    
    # Cloud Run si aspetta una risposta HTTP 200 OK.
    return Response(status=200)

# Endpoint per il health check di Cloud Run (opzionale ma consigliato)
@app.route("/_ah/health")
def health_check():
    return "ok", 200

# Blocco principale per l'avvio del server Flask
if __name__ == "__main__":
    # Inizializza il database all'avvio del contenitore
    init_db()

    # Imposta il webhook di Telegram al primo avvio del server
    # Questo dice a Telegram dove inviare gli aggiornamenti.
    # È un'operazione che va fatta una sola volta o ogni volta che l'URL del servizio cambia.
    try:
        # Await la chiamata a set_webhook in un contesto asincrono
        asyncio.run(application.bot.set_webhook(url=WEBHOOK_TELEGRAM_URL))
        logger.info(f"Webhook di Telegram impostato su: {WEBHOOK_TELEGRAM_URL}")
    except Exception as e:
        logger.error(f"Errore nell'impostazione del webhook di Telegram: {e}")
        # Non uscire, il server Flask deve comunque avviarsi per ricevere richieste.

    # Avvia l'applicazione Flask.
    # Cloud Run fornirà la PORT come variabile d'ambiente.
    # Flask si metterà in ascolto su tutte le interfacce (0.0.0.0) sulla porta specificata.
    PORT = int(os.environ.get("PORT", 8080))
    logger.info(f"Avvio del server Flask sulla porta {PORT}")
    app.run(host="0.0.0.0", port=PORT)
