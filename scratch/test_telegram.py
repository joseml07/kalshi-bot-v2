import asyncio
import httpx
from kalshi_bot.alerts.telegram import TelegramAlerter

async def main():
    token = "8460162595:AAE1voFwnPPnQouucFsFqK7-Et6IPmqnDnk"
    chat_id = "8634797386"
    
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                url,
                params={
                    "offset": "0",
                    "timeout": "30",
                    "allowed_updates": '["message"]',
                },
                timeout=40.0,
            )
            print("Status code:", resp.status_code)
            print("JSON:", resp.json())
        except Exception as e:
            print("Exception:", e)

if __name__ == "__main__":
    asyncio.run(main())
