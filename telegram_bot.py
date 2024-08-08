import os
import logging
import tempfile
import shutil
import pdfplumber
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Define conversation states
COLLECT_FILES, PRINT_TYPE, QUANTITY, SIDES, CONFIRM = range(5)

# Initialize user data store
user_data_store = {}

# Start command handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.message.from_user.id
    username = update.message.from_user.username or update.message.from_user.first_name
    user_data_store[user_id] = {'files': [], 'chat_id': update.message.chat_id, 'username': username}
    await update.message.reply_text(
        'Send me the files you want to print, and I will guide you through the rest. '
        'When you are done, type /done.')
    return COLLECT_FILES

# Collect files handler
async def collect_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.message.from_user.id
    document = update.message.document
    user_data_store[user_id]['files'].append(document)

    await update.message.reply_text('File received. Send more files or type /done to proceed.')
    return COLLECT_FILES

# Done command handler
async def done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.message.from_user.id
    if user_id not in user_data_store or not user_data_store[user_id]['files']:
        await update.message.reply_text('No files received. Please send files first.')
        return COLLECT_FILES

    context.user_data['file_index'] = 0
    await send_print_type_keyboard(update, context)
    return PRINT_TYPE

# Send print type keyboard
async def send_print_type_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [
            InlineKeyboardButton("Color", callback_data='color'),
            InlineKeyboardButton("B&W", callback_data='b&w'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Select Print Type:', reply_markup=reply_markup)

# Print type handler
async def print_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    file_index = context.user_data['file_index']
    file_data = user_data_store[user_id]['files'][file_index]

    user_data_store[user_id][file_data.file_id] = {'print_type': query.data}
    await send_quantity_keyboard(query, context)
    return QUANTITY

# Send quantity keyboard
async def send_quantity_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton(f"{i} copies", callback_data=str(i)) for i in range(1, 6)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.edit_message_text('Select Quantity:', reply_markup=reply_markup)

# Quantity handler
async def quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    file_index = context.user_data['file_index']
    file_data = user_data_store[user_id]['files'][file_index]

    user_data_store[user_id][file_data.file_id]['quantity'] = int(query.data)
    await send_sides_keyboard(query, context)
    return SIDES

# Send sides keyboard
async def send_sides_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [
            InlineKeyboardButton("One Sided", callback_data='one_sided'),
            InlineKeyboardButton("Two Sided", callback_data='two_sided'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.edit_message_text('Select Sides:', reply_markup=reply_markup)

# Sides handler
async def sides(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    file_index = context.user_data['file_index']
    file_data = user_data_store[user_id]['files'][file_index]

    user_data_store[user_id][file_data.file_id]['sides'] = query.data
    context.user_data['file_index'] += 1

    # Check if there are more files to process
    if context.user_data['file_index'] < len(user_data_store[user_id]['files']):
        await send_print_type_keyboard(query, context)
        return PRINT_TYPE
    else:
        await calculate_cost(query, context, user_id)
        return CONFIRM

# Calculate total cost
async def calculate_cost(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> int:
    total_cost = 0
    total_pages = 0
    for file_data in user_data_store[user_id]['files']:
        details = user_data_store[user_id][file_data.file_id]

        # Download the file
        file = await context.bot.get_file(file_data.file_id)
        temp_dir = tempfile.gettempdir()
        file_path = os.path.join(temp_dir, file_data.file_name)
        
        try:
            await file.download_to_drive(file_path)
        except Exception as e:
            logger.error(f"Failed to download file: {e}")
            await update.message.reply_text(f"Failed to download file: {e}")
            continue

        # Count pages of the file
        with pdfplumber.open(file_path) as pdf:
            num_pages = len(pdf.pages)
        
        total_pages += num_pages * details['quantity']
        if details['print_type'] == 'color':
            total_cost += num_pages * 5 * details['quantity']
        else:
            total_cost += num_pages * 1 * details['quantity']

        os.remove(file_path)

    # Store total cost in user data
    context.user_data['total_cost'] = total_cost

    keyboard = [
        [InlineKeyboardButton("Yes", callback_data='yes')],
        [InlineKeyboardButton("No", callback_data='no')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.edit_message_text(f"The total cost is {total_cost} units. Do you want to proceed?", reply_markup=reply_markup)
    return CONFIRM

# Confirm handler
async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == 'yes':
        user_id = query.from_user.id
        await process_files(query, context, user_id)
        await query.edit_message_text("Files are being processed and forwarded.")
        return ConversationHandler.END
    else:
        await query.edit_message_text("Process cancelled.")
        return ConversationHandler.END

# Process files
async def process_files(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    username = user_data_store[user_id]['username']
    temp_dir = tempfile.gettempdir()
    for file_data in user_data_store[user_id]['files']:
        details = user_data_store[user_id][file_data.file_id]

        # Download the file
        file = await context.bot.get_file(file_data.file_id)
        file_path = os.path.join(temp_dir, file_data.file_name)
        
        try:
            await file.download_to_drive(file_path)
        except Exception as e:
            logger.error(f"Failed to download file: {e}")
            await update.message.reply_text(f"Failed to download file: {e}")
            continue

        # Rename the file with user inputs and username
        new_name = f"{username}_{details['print_type']}_{details['quantity']}copies_{details['sides']}.{file_data.file_name.split('.')[-1]}"
        new_path = os.path.join(temp_dir, new_name)
        
        try:
            shutil.move(file_path, new_path)
        except Exception as e:
            logger.error(f"Failed to rename file: {e}")
            await update.message.reply_text(f"Failed to rename file: {e}")
            continue

        # Forward the renamed file to your Telegram account
        try:
            with open(new_path, 'rb') as file:
                await context.bot.send_document(chat_id=ADMIN_USER_ID, document=file, caption='Renamed File')
        except Exception as e:
            logger.error(f"Failed to send document to admin: {e}")
            await update.message.reply_text(f"Failed to send document to admin: {e}")
            continue

        # Forward the renamed file to the user
        try:
            with open(new_path, 'rb') as file:
                await context.bot.send_document(chat_id=user_data_store[user_id]['chat_id'], document=file, caption='Renamed File')
        except Exception as e:
            logger.error(f"Failed to send document to user: {e}")
            await update.message.reply_text(f"Failed to send document to user: {e}")
            continue

        # Remove the original file
        os.remove(new_path)

    # Clear user data
    del user_data_store[user_id]
    
    # Notify user of success
    await context.bot.send_message(chat_id=user_id, text='All files have been successfully processed and sent to you.')
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text='All files have been processed and forwarded to the user.')

# Cancel handler
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Operation cancelled.')
    return ConversationHandler.END

def main():
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TOKEN).build()

    # Add conversation handler with the states COLLECT_FILES, PRINT_TYPE, QUANTITY, SIDES, and CONFIRM
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            COLLECT_FILES: [MessageHandler(filters.Document.ALL, collect_files), CommandHandler('done', done)],
            PRINT_TYPE: [CallbackQueryHandler(print_type)],
            QUANTITY: [CallbackQueryHandler(quantity)],
            SIDES: [CallbackQueryHandler(sides)],
            CONFIRM: [CallbackQueryHandler(confirm)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(conv_handler)

    # Start the Bot
    application.run_polling()

if __name__ == '__main__':
    main()
