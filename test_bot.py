import os
from dotenv import load_dotenv
from src.parser import parse_transaction_message
from src.sheets_client import GoogleSheetsClient
import random

# Cargar las variables de entorno para que lea credentials.json y el Sheet ID
load_dotenv()

def test_flujo():
    print("1. Probando el Parser...")
    mensajes = ["15000 transferencia deuda carrete", "10000 uber eats", "20000 맥주", "100000 bono"]
    texto_prueba = random.choice(mensajes)
    print(f"Texto a parsear: '{texto_prueba}'")
    transaction_id = random.randint(1000, 2000)
    transaction = parse_transaction_message(texto_prueba, message_id="TEST-" + str(transaction_id))
    print(f"Transacción parseada: {transaction.model_dump()}")
    
    print("\n2. Probando conexión a Google Sheets...")
    try:
        client = GoogleSheetsClient()
        # Intentar enviar la transacción a la pestaña 'Gastos'
        exito = client.append_transaction(transaction, sheet_name="Gastos")
        if exito:
            print("¡ÉXITO! La transacción se guardó en Google Sheets.")
        else:
            print("FALLÓ la escritura en Google Sheets.")
    except Exception as e:
        print(f"Error de conexión o escritura: {e}")

if __name__ == "__main__":
    test_flujo()
