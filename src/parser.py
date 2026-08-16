import re
from decimal import Decimal
from typing import Optional
from src.models import Transaction, TipoTransaccion, MetodoPago
import json
import os

# Cargar el mapeo desde el archivo de configuración
def load_category_map() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'categories.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            raw_map = json.load(f)
            
        # Invertir el diccionario: de {"Categoria": ["kw1", "kw2"]} a {"kw1": "Categoria", "kw2": "Categoria"}
        inverted_map = {}
        for category, keywords in raw_map.items():
            for kw in keywords:
                inverted_map[kw.lower()] = category
        return inverted_map
    except FileNotFoundError:
        # Fallback de emergencia si no se encuentra el archivo
        return {"uber": "Transporte"}

CATEGORY_MAP = load_category_map()

def parse_transaction_message(text: str, message_id: str) -> Transaction:
    """
    Parsea un mensaje de texto natural y retorna un modelo Transaction.
    Ejemplo input: "15000 uber" -> Egreso, Transporte
    Ejemplo input: "+15000 reembolso uber" -> Ingreso, Transporte
    """
    text = text.lower().strip()
    
    # 1. Extraer el monto. Busca números que pueden tener un signo '+' o '-' delante.
    # Expresión regular para capturar el primer número (entero o decimal).
    monto_match = re.search(r'([+-]?\d+(?:\.\d+)?)', text)
    if not monto_match:
        raise ValueError("No pude encontrar un monto en el mensaje.")
        
    monto_str = monto_match.group(1)
    
    # Determinar tipo
    tipo = TipoTransaccion.EGRESO
    if monto_str.startswith('+') or "reembolso" in text or "ingreso" in text:
        tipo = TipoTransaccion.INGRESO
        
    
        
    monto_puro = Decimal(monto_str.replace('+', '').replace('-', ''))
    
    # 2. Extraer concepto y categoría (Heurística simple)
    # Quitamos el monto del texto para que quede solo el concepto.
    concepto_bruto = text.replace(monto_str, '').strip()
    
    # Buscamos si alguna palabra clave está en el concepto
    categoria = "Otros Gastos" if tipo == TipoTransaccion.EGRESO else "Otros Ingresos"
    for keyword, cat in CATEGORY_MAP.items():
        if keyword in concepto_bruto:
            categoria = cat
            break
    # Si la categoria es remuneraciones, es un ingreso
    if categoria == "Remuneraciones":
        tipo = TipoTransaccion.INGRESO
            
    # Si el concepto quedó vacío después de quitar el número, usamos la categoría.
    concepto = concepto_bruto if concepto_bruto else f"Gasto en {categoria}"

    return Transaction(
        id_transaccion=str(message_id),
        tipo=tipo,
        monto=monto_puro,
        concepto=concepto.capitalize(),
        categoria=categoria,
        metodo=MetodoPago.DEBITO # Por defecto, luego el usuario puede cambiarlo
    )
