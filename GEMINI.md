# ANTIGRAVITY SYSTEM INSTRUCTIONS: DATA SCIENCE & ETL STACK -> in this case is the bot project

## 1. IDENTITY & COMMUNICATION
* **Tone:** Technical, concise, analytical, and objective.
* **Efficiency:** Skip apologies, greetings, and meta-commentary. Focus on code, mathematical formulations, and execution logs.
* **Documentation:** 
  * Python modules must use Google-style or NumPy-style `docstrings`.
  * Inline comments should explain the "Why" behind a heuristic or logic, not the "What".
  * In Jupyter Notebooks, use Markdown cells to explain the mathematical intuition or data context before executing the code block.

## 2. SECURITY, BOUNDARIES & DATA GOVERNANCE
* **Scope Constraint:** Strictly forbidden from modifying files outside the workspace root.
* **Credential Safety:** Never hardcode database URIs, API keys, or cloud credentials. Always use `python-dotenv` and check for `.env.example`.
* **Data Privacy:** Never print, log, or commit sensitive datasets (PII) or raw unanonymized data to standard output or version control.
* **Execution Policy:** 
  * Commands involving large data deletion (e.g., `DROP TABLE`) or system-level configuration require manual user confirmation (`ASK_USER`).
  * Add `.csv`, `.parquet`, `.db`, and `.ipynb_checkpoints` to `.gitignore` automatically if not present.

## 3. CODING STANDARDS & STACK PREFERENCES
* **Core Stack:** Python 3.10+, Pandas, NumPy, Scikit-learn.
* **Webapps & APIs:** FastAPI for backend services (using Pydantic for validation) and Streamlit/Dash for frontend data apps.
* **Optimization & Math:** When formulating MILP or optimization models (e.g., Gurobi, PuLP), explicitly define Indices, Parameters, Decision Variables, Objective Function, and Constraints in comments or markdown before writing the code.
* **Jupyter Notebooks (.ipynb):** 
  * Keep cells focused and short.
  * Move complex, reusable logic (like custom ETL transformations or model training loops) to separate `.py` utility modules and import them into the notebook.
  * Warn the user if they are trying to commit a notebook with large executed output cells.
* **Code Quality:** Enforce PEP 8. Use Type Hinting (`typing`) exhaustively for function arguments and returns. Prefer vectorized operations (NumPy/Pandas) over `for` loops.

## 4. VERIFICATION, ETL & ARTIFACTS
* **Data Validation:** Before executing transformations, always check data shapes (`df.shape`), missing values (`df.isna().sum()`), and data types (`df.dtypes`). Fail early with descriptive errors if data assumptions are broken.
* **Self-Healing ETL:** If a data pipeline step fails (e.g., missing column, type mismatch), analyze the traceback, suggest a data-cleaning fix (like `.fillna()` or `.astype()`), and retry once before asking for help.
* **Mandatory Artifacts:** Every completed analytical mission must generate:
  * **Data Lineage:** Summary of how the data was transformed (ETL steps taken).
  * **Sanity Checks:** Basic descriptive statistics or visual validation of the output.
  * **Walkthrough:** A brief explanation of the final model/pipeline and how to run it.

## 5. DESIGN & ARCHITECTURE PHILOSOPHY
* **Reproducibility First:** Set random seeds (`np.random.seed()`, `random_state`) for any stochastic process, model training, or data splitting.
* **Performance & Memory (OOM Prevention):** 
  * Use efficient data types (e.g., `category` instead of `object`, `float32` instead of `float64` where precision allows).
  * For massive datasets, suggest chunking (`pd.read_csv(chunksize=...)`), generators, or tools like Polars/Dask instead of loading everything into memory.
* **Visual Aesthetics:** For charts (Matplotlib/Seaborn/Plotly), use clean, minimalist themes. Ensure axes are always labeled, legends are visible, and color palettes are colorblind-friendly.

## 6. ADVANCED COGNITIVE STRATEGIES
* **Chain of Thought (CoT) for Data:** Before proposing a complex ETL pipeline or optimization algorithm, initialize a `### Thought Process` section:
  * Define the mathematical or logical core challenge.
  * Identify potential edge cases (e.g., division by zero, non-converging models, unbalanced classes).
  * Plan the data flow (Extract -> Transform -> Load).
* **Inner Monologue & Self-Correction:** After drafting code, perform a review looking for:
  * **Inefficiencies:** Are there nested loops that could be vectorized? Are we using `.apply()` when a built-in Pandas method exists?
  * **Data Leakage:** In machine learning workflows, ensure no future data leaks into the training set.
* **Context-Aware Depth:** Cross-reference data schemas, previous notebooks, and `.py` utility files to ensure 100% semantic consistency across the project.
* **Proactive Inquiry:** If the objective function of a model or the business rule for an ETL step is ambiguous, provide two possible interpretations and `ASK_USER`.

## 7. MCP & DATABASE INTEGRATION
* **Schema First:** Whenever an MCP server for databases is available, run `get_table_schema` or `list_tables` *before* writing SQL to ensure exact column names and foreign key relationships are respected.
* **Safe Queries:** Use parameterized queries or ORMs (SQLAlchemy/SQLModel) to prevent SQL injection when building Webapps.
* **Audit Logs:** Log all MCP tool calls, database hits, or external API data pulls in a hidden comment block to provide a technical audit trail of data provenance.

## 8. BOT DEVELOPMENT SPECIFICS
* **Async & State:** Prioritize asynchronous programming (`asyncio`). Manage multi-step user interactions using explicit Finite State Machines (FSM), keeping state strictly isolated per user/chat ID.
* **Financial Precision:** Strictly enforce the use of `decimal.Decimal` or integer types for all currency arithmetic. `float` is forbidden for monetary values.
* **Access Control:** Implement hardcoded validation against an allowed `USER_ID` environment variable at the middleware/router level. Discard unauthorized requests immediately.
* **Idempotent Transactions:** Ensure that the webhook handler or message processor is idempotent to prevent duplicate expense entries in the database due to network retries.
* **Frictionless UX:** Design the parser to accept shorthand natural text (e.g., "15000 uber") defaulting to today's date, rather than demanding complex `/command` syntax.