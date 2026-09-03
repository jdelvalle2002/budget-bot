"""Herramienta CLI para testear la generación de comentarios y creatividad de Gemini.

Permite simular el flujo de procesamiento de un mensaje de gasto y evaluar
la respuesta del bot bajo distintas temperaturas y escenarios presupuestarios.
"""

import argparse
import os
import sys
from decimal import Decimal
from typing import List, Optional
from dotenv import load_dotenv

from src.models import format_currency
from src.parser import parse_transaction_message, generar_comentario_ironico, ANGULOS_COMICOS

# Cargar variables de entorno locales
load_dotenv()

EJEMPLOS_PREDETERMINADOS = [
    "ayer me gasté 15 lucas en unas cervezas pagadas con crédito",
    "45000 en el supermercado líder",
    "15000 uber al trabajo porque me quedé dormido",
    "3500 café de especialidad y una galleta",
    "80 lucas en unas zapatillas deportivas con tarjeta",
    "2000 metro bip",
    "pagué 25000 en la farmacia por medicamentos",
]

def parsear_o_construir_transaccion(
    texto_prompt: str,
    monto_manual: Optional[Decimal] = None,
    concepto_manual: Optional[str] = None,
    categoria_manual: Optional[str] = None
):
    """Extrae o construye los datos de la transacción para el comentario.

    Args:
        texto_prompt: Texto natural del gasto ingresado por el usuario.
        monto_manual: Monto forzado opcional.
        concepto_manual: Concepto forzado opcional.
        categoria_manual: Categoría forzada opcional.

    Returns:
        tuple[Decimal, str, str]: Tupla con (monto, concepto, categoria).
    """
    if monto_manual is not None and concepto_manual and categoria_manual:
        return monto_manual, concepto_manual, categoria_manual

    print(f"\n🔎 Analizando input: \"{texto_prompt}\"")
    try:
        resultado = parse_transaction_message(texto_prompt, message_id="TEST-COMMENT-01")
        tx = resultado.transaction
        
        monto = monto_manual if monto_manual is not None else tx.monto
        concepto = concepto_manual if concepto_manual else tx.concepto
        categoria = categoria_manual if categoria_manual else tx.categoria
        
        print(f"   ├─ Monto detectado:     {format_currency(monto)}")
        print(f"   ├─ Concepto detectado:  {concepto}")
        print(f"   ├─ Categoría detectada: {categoria}")
        print(f"   ├─ Método de pago:      {tx.metodo.value}")
        print(f"   └─ Fecha:               {tx.fecha}")
        
        if resultado.es_ambiguo:
            print(f"   ⚠️ Nota: Detección ambigua. Opciones: {resultado.opciones_categoria}")
            
        return monto, concepto, categoria
    except Exception as e:
        print(f"⚠️ Error extrayendo transacción con Gemini: {e}")
        print("Usando valores de contingencia para la prueba de comentario.")
        return Decimal("15000"), texto_prompt, "Otros Gastos"

def main():
    """Punto de entrada principal para la prueba de comentarios."""
    parser = argparse.ArgumentParser(
        description="Test de generación de comentarios irónicos con control de temperatura."
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Texto del gasto (ej: '15 lucas en mcdonalds'). Si se omite, se solicitará de forma interactiva."
    )
    parser.add_argument(
        "--temp", "-t",
        type=float,
        default=None,
        help="Temperatura de Gemini para controlar creatividad (ej: 0.2, 0.9, 1.2). Default lee GEMINI_TEMPERATURE o 0.9."
    )
    parser.add_argument(
        "--samples", "-n",
        type=int,
        default=1,
        help="Número de variaciones de comentario a generar con la misma configuración."
    )
    parser.add_argument(
        "--delta", "-d",
        type=float,
        default=0.1,
        help="Variación delta de temperatura hacia arriba y abajo para comparar (default: 0.1)."
    )
    parser.add_argument(
        "--single", "-s",
        action="store_true",
        help="Evalúa únicamente la temperatura base sin variaciones +-delta."
    )
    parser.add_argument(
        "--compare", "-c",
        action="store_true",
        help="Compara un rango amplio de temperaturas (0.3, 0.6, 0.9, 1.2)."
    )
    parser.add_argument(
        "--all-angles", "-a",
        action="store_true",
        help="Genera un comentario por cada uno de los 5 ángulos cómicos disponibles."
    )
    parser.add_argument(
        "--anomalo",
        action="store_true",
        help="Simula alerta de gasto anómalo (superó promedio mensual + 50%%)."
    )
    parser.add_argument(
        "--excedido",
        action="store_true",
        help="Simula alerta de presupuesto mensual excedido."
    )
    parser.add_argument(
        "--presupuesto",
        type=str,
        default=None,
        help="Mensaje de estado de presupuesto personalizado."
    )
    parser.add_argument(
        "--monto",
        type=str,
        default=None,
        help="Monto explícito (omite detección de monto por LLM)."
    )
    parser.add_argument(
        "--concepto",
        type=str,
        default=None,
        help="Concepto explícito (omite detección de concepto por LLM)."
    )
    parser.add_argument(
        "--categoria",
        type=str,
        default=None,
        help="Categoría explícita (omite detección de categoría por LLM)."
    )

    args = parser.parse_args()

    # Determinar texto del prompt
    if args.prompt:
        texto_prompt = " ".join(args.prompt)
    else:
        print("=" * 60)
        print("  🧪 TEST DE COMENTARIOS Y CREATIVIDAD (BUDGET BOT)")
        print("=" * 60)
        print("Ejemplos rápidos disponibles:")
        for idx, ej in enumerate(EJEMPLOS_PREDETERMINADOS, start=1):
            print(f"  [{idx}] {ej}")
        print("=" * 60)
        
        try:
            seleccion = input("\nEscribe tu prompt de gasto (o número 1-7, Enter para ejemplo aleatorio): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nOperación cancelada.")
            sys.exit(0)

        if not seleccion:
            import random
            texto_prompt = random.choice(EJEMPLOS_PREDETERMINADOS)
            print(f"Usando ejemplo aleatorio: \"{texto_prompt}\"")
        elif seleccion.isdigit() and 1 <= int(seleccion) <= len(EJEMPLOS_PREDETERMINADOS):
            texto_prompt = EJEMPLOS_PREDETERMINADOS[int(seleccion) - 1]
            print(f"Seleccionado: \"{texto_prompt}\"")
        else:
            texto_prompt = seleccion

    monto_decimal = Decimal(args.monto) if args.monto else None
    monto, concepto, categoria = parsear_o_construir_transaccion(
        texto_prompt,
        monto_manual=monto_decimal,
        concepto_manual=args.concepto,
        categoria_manual=args.categoria
    )

    # Configuración de contexto
    estado_presupuesto = args.presupuesto
    if args.excedido and not estado_presupuesto:
        estado_presupuesto = f"Lleva gastado {format_currency(monto * 3)} en el mes, y su límite es {format_currency(monto * 2)}. ¡Se excedió!"

    # Temperaturas a evaluar
    temp_env_raw = os.getenv("GEMINI_TEMPERATURE", "0.9")
    try:
        temp_env = float(temp_env_raw)
    except ValueError:
        temp_env = 0.9

    base_temp = args.temp if args.temp is not None else temp_env
    delta = abs(args.delta)

    if args.compare:
        temperaturas = [0.3, 0.6, 0.9, 1.2]
    elif args.single:
        temperaturas = [base_temp]
    else:
        # Por defecto muestra la temperatura seteada y su entorno +- delta
        t_low = max(0.0, round(base_temp - delta, 2))
        t_mid = round(base_temp, 2)
        t_high = min(2.0, round(base_temp + delta, 2))
        # Conservar orden y remover duplicados si t_low == t_mid
        temperaturas = []
        for t in [t_low, t_mid, t_high]:
            if t not in temperaturas:
                temperaturas.append(t)

    print("\n" + "=" * 60)
    print("  💬 GENERACIÓN DE COMENTARIOS")
    print("=" * 60)
    print(f"Tono configurado:    {os.getenv('BOT_TONE', 'crítico, constructivo y manteniendo un toque humorístico y con sarcasmo y/o ironía')}")
    print(f"Contexto configurado:{os.getenv('BOT_CONTEXT', 'Chile, usando pesos chilenos sin decimales.')}")
    if args.anomalo:
        print("Alerta:              🚨 ANOMALÍA ACTIVADA")
    if estado_presupuesto:
        print(f"Presupuesto:         ⚠️ {estado_presupuesto}")
    print("-" * 60)

    if args.all_angles:
        print(f"\n🎭 Evaluando los 5 ángulos cómicos (Temperatura fija = {base_temp:.2f}):")
        for idx, angulo in enumerate(ANGULOS_COMICOS, start=1):
            print(f"\n[Ángulo {idx}/5]: {angulo}")
            comentario = generar_comentario_ironico(
                monto=monto,
                concepto=concepto,
                categoria=categoria,
                estado_presupuesto=estado_presupuesto,
                es_anomalo=args.anomalo,
                temperature=base_temp,
                angulo=angulo
            )
            if comentario:
                print(f"🤖  \"{comentario}\"")
            else:
                print("❌  No se generó ningún comentario (verifica tu GEMINI_API_KEY).")
    else:
        for temp in temperaturas:
            for i in range(args.samples):
                subindice = f" (Muestra {i+1}/{args.samples})" if args.samples > 1 else ""
                print(f"\n[Temperatura = {temp:.2f}]{subindice}")
                
                comentario = generar_comentario_ironico(
                    monto=monto,
                    concepto=concepto,
                    categoria=categoria,
                    estado_presupuesto=estado_presupuesto,
                    es_anomalo=args.anomalo,
                    temperature=temp
                )
                
                if comentario:
                    print(f"🤖  \"{comentario}\"")
                else:
                    print("❌  No se generó ningún comentario (verifica tu GEMINI_API_KEY).")
                
    print("\n" + "=" * 60 + "\n")

if __name__ == "__main__":
    main()
