"""Разовый вход агента юзербота: `python -m musicbot.userbot_login`.
Спросит api_id, api_hash, номер и код из Telegram, затем напечатает строку сессии для USERBOT_SESSION.
Запускать у себя на компьютере: на хостинге ввести код из SMS негде."""

from telethon import TelegramClient
from telethon.sessions import StringSession


def main() -> None:
    api_id = int(input("api_id (my.telegram.org): ").strip())
    api_hash = input("api_hash: ").strip()
    with TelegramClient(StringSession(), api_id, api_hash) as client:  # start() сам спросит номер, код и пароль
        print("\nUSERBOT_SESSION=" + client.session.save())
    print("\nСтрока выше открывает доступ к аккаунту: храните как токен бота и никому не показывайте.")


if __name__ == "__main__":
    main()
