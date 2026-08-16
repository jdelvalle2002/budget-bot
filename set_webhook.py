import os
import sys
import httpx
import asyncio
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

async def set_webhook(ngrok_url: str):
    # La ruta de nuestro webhook definida en main.py es /webhook/{TOKEN}
    webhook_url = f"{ngrok_url.rstrip('/')}/webhook/{TELEGRAM_TOKEN}"
    
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    
    print(f"Configurando webhook a: {webhook_url}")
    
    async with httpx.AsyncClient() as client:
        response = await client.post(api_url, json={"url": webhook_url})
        if response.status_code == 200 and response.json().get("ok"):
            print("✅ Webhook configurado exitosamente en Telegram.")
        else:
            print(f"❌ Error al configurar webhook: {response.text}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python set_webhook.py <TU_URL_DE_NGROK_AQUI>")
        print("Ejemplo: python set_webhook.py https://1234-abcd.ngrok.app")
        sys.exit(1)
        
    url = sys.argv[1]
    asyncio.run(set_webhook(url))
