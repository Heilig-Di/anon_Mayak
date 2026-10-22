import asyncio
import logging
import os
import random
import sys
from typing import Optional, Dict

from aiogram import Bot, Dispatcher, Router, F
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command
from aiogram.types import Message, ReplyParameters
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ---------- Конфигурация ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID", "-1002601127053"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = "/webhook"
PORT = int(os.getenv("PORT", 8000))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Хранилище маппинга оригинал -> анонимное сообщение
message_map: Dict[int, int] = {}

# ИЗМЕНЕНИЕ 1: глобальная очередь для отправки сообщений
send_queue: asyncio.Queue = asyncio.Queue()


def generate_alias() -> str:
    return f"Маячок{random.randint(1, 999)}"


async def is_admin_or_channel(message: Message) -> bool:
    if message.sender_chat is not None:
        logger.debug("Сообщение от sender_chat (канал/анонимный админ)")
        return True
    if message.from_user is None:
        logger.debug("Сообщение без from_user")
        return True
    if message.from_user.is_bot:
        logger.debug("Сообщение от бота")
        return True
    try:
        member = await bot.get_chat_member(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
        )
        if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            logger.debug(f"Пользователь {message.from_user.id} — администратор")
            return True
    except Exception as e:
        logger.warning(f"Не удалось проверить статус пользователя {message.from_user.id}: {e}")
    return False


async def delete_message_safe(chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Сообщение {message_id} удалено")
    except TelegramBadRequest as e:
        if "message to delete not found" in str(e).lower():
            logger.debug(f"Сообщение {message_id} уже удалено")
        else:
            logger.error(f"Ошибка удаления {message_id}: {e}")
    except Exception as e:
        logger.error(f"Неизвестная ошибка при удалении {message_id}: {e}")


async def resend_message(message: Message, alias: str, reply_to: Optional[int] = None) -> Optional[Message]:
    reply_params = ReplyParameters(message_id=reply_to) if reply_to else None
    try:
        if message.text:
            text = f"<b>{alias}</b>\n{message.text}"
            return await bot.send_message(
                chat_id=message.chat.id,
                text=text,
                reply_parameters=reply_params,
                disable_web_page_preview=True,
            )
        elif message.photo:
            caption = message.caption or ""
            new_caption = f"<b>{alias}</b>\n{caption}" if caption else ""
            return await bot.send_photo(
                chat_id=message.chat.id,
                photo=message.photo[-1].file_id,
                caption=new_caption,
                reply_parameters=reply_params,
            )
        elif message.video:
            caption = message.caption or ""
            new_caption = f"<b>{alias}</b>\n{caption}" if caption else ""
            return await bot.send_video(
                chat_id=message.chat.id,
                video=message.video.file_id,
                caption=new_caption,
                reply_parameters=reply_params,
            )
        elif message.sticker:
            return await bot.send_sticker(
                chat_id=message.chat.id,
                sticker=message.sticker.file_id,
                reply_parameters=reply_params,
            )
        elif message.voice:
            return await bot.send_voice(
                chat_id=message.chat.id,
                voice=message.voice.file_id,
                reply_parameters=reply_params,
            )
        elif message.video_note:
            return await bot.send_video_note(
                chat_id=message.chat.id,
                video_note=message.video_note.file_id,
                reply_parameters=reply_params,
            )
        elif message.document:
            caption = message.caption or ""
            new_caption = f"<b>{alias}</b>\n{caption}" if caption else ""
            return await bot.send_document(
                chat_id=message.chat.id,
                document=message.document.file_id,
                caption=new_caption,
                reply_parameters=reply_params,
            )
        elif message.audio:
            caption = message.caption or ""
            new_caption = f"<b>{alias}</b>\n{caption}" if caption else ""
            return await bot.send_audio(
                chat_id=message.chat.id,
                audio=message.audio.file_id,
                caption=new_caption,
                reply_parameters=reply_params,
            )
        elif message.animation:
            caption = message.caption or ""
            new_caption = f"<b>{alias}</b>\n{caption}" if caption else ""
            return await bot.send_animation(
                chat_id=message.chat.id,
                animation=message.animation.file_id,
                caption=new_caption,
                reply_parameters=reply_params,
            )
        elif message.location:
            return await bot.send_location(
                chat_id=message.chat.id,
                latitude=message.location.latitude,
                longitude=message.location.longitude,
                reply_parameters=reply_params,
            )
        elif message.contact:
            return await bot.send_contact(
                chat_id=message.chat.id,
                phone_number=message.contact.phone_number,
                first_name=message.contact.first_name,
                reply_parameters=reply_params,
            )
        elif message.poll:
            return await bot.send_poll(
                chat_id=message.chat.id,
                question=message.poll.question,
                options=[opt.text for opt in message.poll.options],
                is_anonymous=message.poll.is_anonymous,
                type=message.poll.type,
                allows_multiple_answers=message.poll.allows_multiple_answers,
                correct_option_id=message.poll.correct_option_id,
                explanation=message.poll.explanation,
                reply_parameters=reply_params,
            )
        else:
            logger.debug(f"Неподдерживаемый тип контента: {message.content_type}")
            return None
    except Exception as e:
        logger.error(f"Ошибка при переотправке сообщения: {e}")
        return None


# ИЗМЕНЕНИЕ 2: фоновый воркер, который обрабатывает очередь по одной задаче
async def sender_worker():
    """
    Берёт задачи из очереди и обрабатывает их строго по одной
    с паузой 1 секунда, чтобы не превышать лимиты Telegram и не ловить 429.
    """
    logger.info("Sender worker запущен")
    while True:
        try:
            message, alias, reply_to = await send_queue.get()
            sent_message = await resend_message(message, alias, reply_to)

            if sent_message:
                await delete_message_safe(message.chat.id, message.message_id)
                message_map[message.message_id] = sent_message.message_id
                logger.info(f"✅ Сообщение {message.message_id} -> {sent_message.message_id} ({alias})")
            else:
                logger.error(f"❌ Не удалось отправить для {message.message_id}, оригинал не удалён")

            # Пауза между отправками — ключевой момент против 429
            await asyncio.sleep(1.0)
            send_queue.task_done()
        except Exception as e:
            logger.error(f"Ошибка в sender_worker: {e}")
            await asyncio.sleep(1.0)


# ---------- Обработчик сообщений ----------
@router.message(F.chat.id == GROUP_ID)
async def handle_group_message(message: Message):
    logger.info(f"Получено сообщение {message.message_id} от {message.from_user.id if message.from_user else 'канала'}")

    if message.from_user and message.from_user.id == bot.id:
        logger.debug("Сообщение от бота — игнорируем")
        return

    if await is_admin_or_channel(message):
        logger.info("Сообщение от админа/канала/бота — игнорируем")
        return

    reply_to = None
    if message.reply_to_message:
        orig_reply_id = message.reply_to_message.message_id
        reply_to = message_map.get(orig_reply_id)
        if reply_to is None:
            if message.reply_to_message.forward_from_chat:
                reply_to = orig_reply_id
                logger.info(f"Ответ на пост канала, reply_to = {reply_to}")
            else:
                logger.info(f"Не найден анонимный ID для {orig_reply_id}, ответ будет без привязки")

    alias = generate_alias()
    logger.info(f"Ставим в очередь отправку от {alias}")

    # ИЗМЕНЕНИЕ 3: кладём задачу в очередь, а не вызываем resend_message напрямую
    await send_queue.put((message, alias, reply_to))


@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Бот для анонимизации активен")


async def on_shutdown():
    logger.info("Завершение работы...")
    await bot.session.close()


async def main():
    if WEBHOOK_URL:
        logger.info("Запуск в режиме Webhook")

        # ИЗМЕНЕНИЕ 4: сбрасываем старый вебхук и накопленные обновления
        await bot.delete_webhook(drop_pending_updates=True)

        # Ставим вебхук с ограничением соединений до 1 — критично для Render Free
        await bot.set_webhook(
            f"{WEBHOOK_URL}{WEBHOOK_PATH}",
            max_connections=1,
            drop_pending_updates=True,
        )

        app = web.Application()
        webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
        webhook_requests_handler.register(app, path=WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
        await site.start()

        logger.info(f"Webhook установлен на {WEBHOOK_URL}{WEBHOOK_PATH} (max_connections=1)")

        # запускаем воркер, который будет разгребать очередь
        asyncio.create_task(sender_worker())

        await asyncio.Event().wait()
    else:
        logger.info("Запуск в режиме Polling")
        asyncio.create_task(sender_worker())
        await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
    finally:
        asyncio.run(on_shutdown())
