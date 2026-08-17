import os
from dotenv import load_dotenv
from src.parser import parse_transaction_message
from src.sheets_client import GoogleSheetsClient
import random

# Cargar las variables de entorno para que lea credentials.json y el Sheet ID
load_dotenv()

import sys

def test_flujo():
    print("1. Probando el Parser con Inteligencia Artificial (Gemini)...")
    mensajes = [
        "ayer me gasté 15 lucas en unas cervezas pagadas con crédito",
        "me devolvieron 10 lucas de la fiesta de ayer",
        "7 gambas en pan pal desayuno",
        "hace 3 días pagué el internet de la casa, 20k con debito",
        "el lunes pasado transferí 50 lucas para la cuota de la cancha",
        "compré una entrada al cine para mañana por 8000 en efectivo", # Test: Gasto futuro (debería fallar o extraer hoy si asume la compra hoy)
        "15000 de bencina didi efectivo",
        "me pagaron el sueldo 500k por transferencia el viernes pasado",
        "reembolso de 45000 por la consulta médica de la semana pasada",
        "gaste 45 lucas en el super líder pagado con la de credito ayer",
        "transferí 20 lucas para el asado del sábado",
        "hace exactamente un mes compré un poleron a 30 lucas", # Test: 30 días, justo al borde
        "en diciembre de 2021 gasté 50k en regalos", # Test: Gasto muy antiguo (debería fallar la validación de 45 días)
        "15k propina uber eats efectivo ayer en la noche",
        "me cobraron la suscripción de fintual hoy 20 lucas",
        "10 luquitas pa la junta de hoy, transferencia",
        "ayer me comí un completo en la calle, 2500 efec",
        "pagué 40k en el cruz verde por medicamentos hace 5 días",
        "me robaron 15 lucas de la billetera ayer",
        "pagué la cuenta de wom 12k con debito",
        "compré verduras en la feria por 15000 al contado ayer en la mañana",
        "30 lucas pa la mesada del mes pasado por transferencia",
        "hace 2 semanas me gasté 80 lucas en ropa deportiva con crédito",
        "mañana me pagarán 100k del bono", # Test: Ingreso en el futuro (debería fallar)
        "2k metro bip",
        "10 lucas de detergente y toallitas en el jumbo, débito hace 2 días",
        "transferencia de 50k a racional ayer",
        "me regalaron 20 lucas por mi cumpleaños el finde",
        "pagué 15k al psicologo hoy con transfe",
        "4 lucas en helado ayer en la tarde",
        "10 luquitas en cervezas del super", # Ambiguo: Alimentación vs Salidas
        "compré un regalo de cumple por 15k con credito", # Ambiguo: Otros Gastos vs Salidas
        "pagué 8 lucas de uber para ir al estadio", # Ambiguo: Transporte vs Salidas vs Deportes
        "30 lucas en unas zapatillas deportivas nuevas", # Ambiguo: Deportes vs Otros Gastos
        "1500 en un café antes de entrar a clases" # Ambiguo: Alimentación vs Salidas
    ]
    
    if len(sys.argv) > 1:
        texto_prueba = " ".join(sys.argv[1:])
    else:
        texto_prueba = random.choice(mensajes)
        
    print(f"\nTexto a parsear: '{texto_prueba}'")
    transaction_id = random.randint(1000, 2000)
    parse_result = parse_transaction_message(texto_prueba, message_id="TEST-" + str(transaction_id))
    transaction = parse_result.transaction
    
    if parse_result.es_ambiguo:
        print(f"\n⚠️ CASO AMBIGUO DETECTADO: Gemini no está seguro.")
        print(f"Opciones propuestas: {parse_result.opciones_categoria}")
    else:
        print(f"\nTransacción parseada: {transaction.model_dump()}")
    
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
