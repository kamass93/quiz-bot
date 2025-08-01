import logging
import random
import sqlite3
import os
import time
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
)

# Configurazione
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = "7305825990:AAHQYjZ54g8TqmEgjV26sPEAF3V_W9FKeVc"
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

init_db()

async def save_score(user_id: int, username: str, category: str, score: int, total: int):
    conn = sqlite3.connect('scores.db')
    c = conn.cursor()
    c.execute("INSERT INTO scores VALUES (?, ?, ?, ?, ?, datetime('now'))",
              (user_id, username, category, score, total))
    conn.commit()
    conn.close()

# Generazione immagine punteggio
async def generate_score_image(user_data: dict, user_id: int):
    try:
        img = Image.new('RGB', (800, 400), color=(58, 95, 205))
        d = ImageDraw.Draw(img)
        
        font_large = ImageFont.truetype("arial.ttf", 40)
        font_medium = ImageFont.truetype("arial.ttf", 30)
        
        d.text((50, 50), "Certificato Quiz Sanitario", fill=(255, 255, 255), font=font_large)
        
        lines = [
            f"Utente: {user_data.get('username', '')}",
            f"Categoria: {user_data.get('category', '').title()}",
            f"Punteggio: {user_data['score']}/{len(user_data['questions'])}",
            f"Data: {datetime.now().strftime('%d/%m/%Y')}"
        ]
        
        for i, line in enumerate(lines):
            d.text((50, 120 + i*50), line, fill=(255, 255, 255), font=font_medium)
        
        img_path = f"temp_score_{user_id}.png"
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
        quiz_df = pd.read_excel("quiz.xlsx")
        categories = quiz_df["categoria"].dropna().unique().tolist()
        
        keyboard = [[InlineKeyboardButton(f"📚 {cat.title()}", callback_data=cat)] for cat in categories]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "👋 Benvenuto al *Quiz Bot!*\n\nScegli una categoria:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return CHOOSE_CATEGORY
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
        with open(image_path, 'rb') as photo:
            img_msg = await context.bot.send_photo(
                chat_id=user_id,
                photo=InputFile(photo),
                caption=" ",
                parse_mode=ParseMode.MARKDOWN
            )
            data["last_messages"].append(img_msg.message_id)
            time.sleep(0.5)  # Piccolo delay per l'effetto visivo

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
    time.sleep(DELAY_BEFORE_NEXT_QUESTION)
    
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
    
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))

async def handle_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = user_data.get(user_id, {})
    
    if not data:
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
                    switch_inline_query=share_text.split('\n\n')[0]
                )
            ]])
        )
    
    elif query.data == "share_image":
        img_path = await generate_score_image(data, user_id)
        if img_path:
            await context.bot.send_photo(
                chat_id=user_id,
                photo=open(img_path, 'rb'),
                caption=f"Condividi il tuo risultato! @{context.bot.username}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "Condividi", 
                        switch_inline_query="Guarda il mio certificato!"
                    )
                ]])
            )
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

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSE_CATEGORY: [CallbackQueryHandler(choose_category)],
            ASK_QUESTION: [CallbackQueryHandler(answer_question)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(share_options, pattern="^share_options$"))
    app.add_handler(CallbackQueryHandler(handle_share, pattern="^(share_text|share_image|show_leaderboard)$"))
    
    logger.info("Bot avviato")
    app.run_polling()

if __name__ == "__main__":
    main()