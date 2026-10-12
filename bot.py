import asyncio
import logging
import os
from collections import OrderedDict

from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher, F  # noqa: E402
from aiogram.filters import CommandStart  # noqa: E402
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from explainer import MAX_INPUT_CHARS, explain_terms, rephrase  # noqa: E402

ALLOWED_USERNAMES = {
    name.strip().lstrip("@").lower()
    for name in os.environ.get("ALLOWED_USERNAMES", "fezsopp").split(",")
    if name.strip()
}

# Режим кнопки -> функция обработки текста.
MODES = {
    "terms": explain_terms,
    "rephrase": rephrase,
}

TELEGRAM_MAX_CHARS = 4096
MAX_STORED_TEXTS = 500

# Тексты сообщений, ожидающих выбора режима: (chat_id, message_id) -> текст.
# В callback_data влезает только 64 байта, поэтому сам текст храним здесь.
# Хранится в памяти: после перезапуска бота старые кнопки перестают работать.
_texts: OrderedDict[tuple[int, int], str] = OrderedDict()

dp = Dispatcher()


def is_allowed(event: Message | CallbackQuery) -> bool:
    user = event.from_user
    return bool(user and user.username and user.username.lower() in ALLOWED_USERNAMES)


def mode_keyboard(message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Термины", callback_data=f"terms:{message_id}"),
        InlineKeyboardButton(text="Перефразировать", callback_data=f"rephrase:{message_id}"),
    ]])


def split_message(text: str) -> list[str]:
    """Режет текст на части, которые влезают в одно сообщение Telegram."""
    parts = []
    while len(text) > TELEGRAM_MAX_CHARS:
        cut = text.rfind("\n", 0, TELEGRAM_MAX_CHARS)
        if cut <= 0:
            cut = TELEGRAM_MAX_CHARS
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    parts.append(text)
    return parts


@dp.message(~F.func(is_allowed))
async def on_forbidden(message: Message) -> None:
    await message.answer("Доступ закрыт.")


@dp.callback_query(~F.func(is_allowed))
async def on_forbidden_callback(callback: CallbackQuery) -> None:
    await callback.answer("Доступ закрыт.", show_alert=True)


@dp.message(CommandStart())
async def on_start(message: Message) -> None:
    await message.answer(
        "Пришли текст и выбери, что с ним сделать:\n"
        "• Термины — объясню незнакомые слова по отдельности;\n"
        "• Перефразировать — перескажу текст простыми словами."
    )


@dp.message(F.text)
async def on_text(message: Message) -> None:
    _texts[(message.chat.id, message.message_id)] = message.text
    while len(_texts) > MAX_STORED_TEXTS:
        _texts.popitem(last=False)
    await message.reply("Что сделать с текстом?", reply_markup=mode_keyboard(message.message_id))


@dp.callback_query(F.data.regexp(r"^(terms|rephrase):\d+$"))
async def on_mode(callback: CallbackQuery) -> None:
    mode, message_id = callback.data.split(":")
    message_id = int(message_id)
    chat_id = callback.message.chat.id
    text = _texts.get((chat_id, message_id))
    if text is None:
        await callback.answer("Текст устарел, пришли его заново.", show_alert=True)
        return
    await callback.answer()

    try:
        answer, truncated = await MODES[mode](text)
    except Exception:
        logging.exception("Не удалось обработать текст")
        await callback.message.answer("Не получилось обработать текст, попробуй позже.")
        return
    if len(text) > MAX_INPUT_CHARS:
        answer += (
            f"\n\n⚠️ Текст длиннее {MAX_INPUT_CHARS} символов: "
            f"обработаны только первые {MAX_INPUT_CHARS}."
        )
    if truncated:
        answer += "\n\n⚠️ Ответ получился слишком длинным и был обрезан. Пришли текст покороче."
    for part in split_message(answer):
        await callback.bot.send_message(chat_id, part, reply_to_message_id=message_id)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=os.environ["BOT_TOKEN"])
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
