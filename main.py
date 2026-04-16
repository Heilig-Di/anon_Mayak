import asyncio
import logging
import os
import random
import sys
from typing import Optional

from aiogram import Bot, Dispatcher, Router, F
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command
from aiogram.types import Message, ReplyParameters
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@dod_mayak")  # в коде не используется, но оставлено для совместимости
GROUP_ID = int(os.getenv("GROUP_ID", "-1002601127053"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")      # если пусто – polling
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



def generate_alias() -> str:
    return f"Маячок{random.randint(1, 999)}"


async def is_admin_or_channel(message: Message) -> bool:
    # Сообщение от канала (анонимный админ)
    if message.from_user is None:
        return True
    # Сообщение от бота
    if message.from_user.is_bot:
        return True

    try:
        member = await bot.get_chat_member(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
        )
        if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return True
    except Exception as e:
        logger.warning(f"Не удалось проверить статус: {e}")

    return False


async def delete_message_safe(chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.debug(f"Сообщение {message_id} удалено")
    except TelegramBadRequest as e:
        if "message to delete not found" in str(e).lower():
            logger.debug(f"Сообщение {message_id} уже было удалено")
        else:
            logger.error(f"Ошибка удаления сообщения {message_id}: {e}")
    except Exception as e:
        logger.error(f"Неизвестная ошибка при удалении {message_id}: {e}")


async def resend_message(message: Message, alias: str):
    # Параметры для ответа (если оригинал был ответом)
    reply_params = None
    if message.reply_to_message:
        reply_params = ReplyParameters(message_id=message.reply_to_message.message_id)

    try:
        # 1. Текст
        if message.text:
            text = f"<b>{alias}</b>\n{message.text}"
            await bot.send_message(
                chat_id=message.chat.id,
                text=text,
                reply_parameters=reply_params,
                disable_web_page_preview=True,
            )

        # 2. Фото
        elif message.photo:
            caption = message.caption or ""
            new_caption = f"<b>{alias}</b>\n{caption}" if caption else ""
            await bot.send_photo(
                chat_id=message.chat.id,
                photo=message.photo[-1].file_id,
                caption=new_caption,
                reply_parameters=reply_params,
            )

        # 3. Видео
        elif message.video:
            caption = message.caption or ""
            new_caption = f"<b>{alias}</b>\n{caption}" if caption else ""
            await bot.send_video(
                chat_id=message.chat.id,
                video=message.video.file_id,
                caption=new_caption,
                reply_parameters=reply_params,
            )

        # 4. Стикер
        elif message.sticker:
            await bot.send_sticker(
                chat_id=message.chat.id,
                sticker=message.sticker.file_id,
                reply_parameters=reply_params,
            )

        # 5. Голосовое сообщение
        elif message.voice:
            await bot.send_voice(
                chat_id=message.chat.id,
                voice=message.voice.file_id,
                reply_parameters=reply_params,
            )

        # 6. Кружок
        elif message.video_note:
            await bot.send_video_note(
                chat_id=message.chat.id,
                video_note=message.video_note.file_id,
                reply_parameters=reply_params,
            )

        # 7. Документ
        elif message.document:
            caption = message.caption or ""
            new_caption = f"<b>{alias}</b>\n{caption}" if caption else ""
            await bot.send_document(
                chat_id=message.chat.id,
                document=message.document.file_id,
                caption=new_caption,
                reply_parameters=reply_params,
            )


        # 8. Аудио
        elif message.audio:
            caption = message.caption or ""
            new_caption = f"<b>{alias}</b>\n{caption}" if caption else ""
            await bot.send_audio(
                chat_id=message.chat.id,
                audio=message.audio.file_id,
                caption=new_caption,
                reply_parameters=reply_params,
            )

        # 9. GIF
        elif message.animation:
            caption = message.caption or ""
            new_caption = f"<b>{alias}</b>\n{caption}" if caption else ""
            await bot.send_animation(
                chat_id=message.chat.id,
                animation=message.animation.file_id,
                caption=new_caption,
                reply_parameters=reply_params,
            )

        # 10. Местоположение
        elif message.location:
            await bot.send_location(
                chat_id=message.chat.id,
                latitude=message.location.latitude,
                longitude=message.location.longitude,
                reply_parameters=reply_params,
            )

        # 11. Контакт
        elif message.contact:
            await bot.send_contact(
                chat_id=message.chat.id,
                phone_number=message.contact.phone_number,
                first_name=message.contact.first_name,
                reply_parameters=reply_params,
            )


        else:
            logger.debug(f"Неподдерживаемый тип контента: {message.content_type}")

    except Exception as e:
        logger.error(f"Ошибка при переотправке сообщения: {e}")


@router.message(F.chat.id == GROUP_ID)
async def handle_group_message(message: Message):
    """
    Обрабатывает любое сообщение в привязанной группе:
    - игнорирует сообщения от самого бота (чтобы не зациклиться);
    - игнорирует админов/ботов/каналы;
    - удаляет оригинал;
    - переотправляет с псевдонимом.
    """
    if message.from_user and message.from_user.id == bot.id:
        return

    if await is_admin_or_channel(message):
        logger.debug("Сообщение от админа/бота/канала — игнорируем")
        return

    await delete_message_safe(message.chat.id, message.message_id)

    alias = generate_alias()
    await resend_message(message, alias)
    logger.info(f"Сообщение от {message.from_user.id} переотправлено как {alias}")


@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Бот для анонимизации активен")


async def on_shutdown():
    logger.info("Завершение работы...")
    await bot.session.close()


async def main():
    if WEBHOOK_URL:
        # Webhook-режим
        logger.info("Запуск в режиме Webhook")
        await bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}")
        app = web.Application()
        webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
        webhook_requests_handler.register(app, path=WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
        await site.start()

        logger.info(f"Webhook установлен на {WEBHOOK_URL}{WEBHOOK_PATH}")
        # Бесконечное ожидание
        await asyncio.Event().wait()
    else:
        # Polling-режим (для локального тестирования)
        logger.info("Запуск в режиме Polling")
        await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
    finally:
        asyncio.run(on_shutdown())