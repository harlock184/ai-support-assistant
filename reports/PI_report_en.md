## Architecture Overview

The application is a command-line support assistant that processes one customer question per execution through a linear, well-defined pipeline. Each stage has a single responsibility, which keeps the code modular and makes failures easy to trace.

The flow of a query is as follows:

1. The user provides a question as a command-line argument to `run_query.py`.
2. Before anything else, the `safety.py` module inspects the question to detect adversarial input (prompt-injection attempts).
3. If the input is flagged as adversarial, the script returns a safe JSON response, logs the attempt to `logs/security_log.jsonl`, and **does not call the OpenAI API**.
4. If the input is legitimate, the prompt is built by combining the system rules, the few-shot examples, and the user's question (loaded from `prompts/main_prompt_v2.txt`).
5. The assembled prompt is sent to the OpenAI API (`gpt-4o-mini`).
6. The model's response is parsed and validated against the five-field JSON contract (`answer`, `confidence`, `actions`, `category`, `needs_human`).
7. Usage metrics — token counts, latency, and estimated cost — are calculated and appended as a new row to `metrics/metrics.csv`.
8. The validated JSON is printed to the console.

This design applies a "two-gate" approach: an input gate (`safety.py`) screens incoming text before it reaches the model, and an output gate (`validar_respuesta`) verifies the model's response before it is delivered. Keeping these checks at the entry and exit points limits the impact of both malicious input and malformed model output.
