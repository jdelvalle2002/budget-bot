# Suite de Pruebas (Test Suite) - Budget Bot

Este directorio contiene las pruebas unitarias, de integración y scripts de verificación del bot.

## Estructura de la Carpeta `tests/`

| Archivo | Tipo | Propósito |
| :--- | :--- | :--- |
| `conftest.py` | Configuración | Fixtures de Pytest. Asegura la inclusión de la raíz en `sys.path` y aísla `os.environ` antes y después de cada test para prevenir efectos colaterales. |
| `test_category_edit_and_adjustments.py` | Unitario / Async | Valida el cambio directo de categoría en edición, soporte de 'CATEGORIA xxxxx' y neteo de reembolsos/ajustes de gastos compartidos. |
| `test_env_comments.py` | Unitario | Valida que la variable de entorno `ENABLE_BOT_COMMENTS` (o `BOT_ENABLE_COMMENTS`) controle adecuadamente la habilitación/deshabilitación de comentarios humorísticos. |
| `test_i18n_formatting.py` | Unitario | Comprueba el formateo de monedas, soporte internacional (CLP, EUR), posiciones de prefijo/sufijo y separadores de miles/decimales. |
| `test_pacing.py` | Unitario / Async | Evalúa la lógica de ritmo de gasto (*burn-rate*), proyección de agotamiento, márgenes seguros diarios y el comando `/ritmo`. |
| `test_payroll.py` | Unitario / Integración | Valida el método de pago `Planilla` (consumos por casino/descuentos laborales), reglas de *fast-path* y no duplicación de balance neto. |
| `test_query_parser.py` | Unitario | Comprueba el motor de consultas naturales (NLQ), lematización y filtrado temporal de transacciones. |
| `test_summary_contributions.py` | Unitario / Async | Verifica la agregación mensual, el neteo automático de aportes/reembolsos y la generación de gráficos. |
| `test_tendencias_and_dates.py` | Unitario / Async | Evalúa el parsing flexible de fechas (ISO, formatos latinos DD-MM-YYYY) y la lógica comparativa del comando `/tendencias`. |
| `test_comments.py` | CLI / Evaluación | Herramienta CLI para inspeccionar y evaluar la creatividad del LLM bajo distintas temperaturas y ángulos cómicos. |
| `test_bot.py` | Manual / E2E | Script de prueba manual de extremo a extremo que interactúa con Google Sheets y Gemini en vivo (ignorado por defecto en ejecuciones automáticas de pytest). |

---

## Ejecución de Pruebas

### 1. Ejecutar toda la suite automática con Pytest

```bash
# Con el entorno virtual activado:
pytest

# O alternativamente:
python -m pytest
```

### 2. Ejecutar un archivo de test específico

```bash
pytest tests/test_env_comments.py -v
```

### 3. Ejecutar herramientas CLI y pruebas manuales

#### Probar creatividad de comentarios con Gemini:
```bash
python tests/test_comments.py --samples 2 --all-angles
```

#### Probar flujo manual directo:
```bash
python tests/test_bot.py
```

---

## Buenas Prácticas y Configuración

- **Aislamiento de Entorno:** Todas las pruebas automáticas restauran `os.environ` a través de la fixture `isolate_environment` en `conftest.py`.
- **Soporte Asíncrono:** La configuración en `pytest.ini` (`asyncio_mode = auto`) gestiona automáticamente la ejecución de corutinas `async def test_*()` mediante `pytest-asyncio`.
