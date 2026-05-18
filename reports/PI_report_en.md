# Integrative Project Report — AI Support Assistant

**Module:** AI Engineering M1 · **Language:** Python 3.11 · **Model:** OpenAI `gpt-4o-mini`

This project implements a command-line customer support assistant for an e-commerce store, built on the OpenAI API. It receives a user question and returns a structured JSON response, while recording usage metrics on every call.

## 1. Architecture Overview

The application processes one customer question per execution through a linear pipeline. Each stage has a single responsibility, which keeps the code modular and makes failures easy to trace.

The flow of a query is as follows:

1. The user provides a question as a command-line argument to `run_query.py`.
2. Before anything else, the `safety.py` module inspects the question to detect adversarial input (prompt-injection attempts).
3. If the input is flagged as adversarial, the script returns a safe JSON response, logs the attempt to `logs/security_log.jsonl`, and **does not call the OpenAI API**.
4. If the input is legitimate, the prompt is built by combining the system rules, the few-shot examples, and the user's question (loaded from `prompts/main_prompt_v2.txt`).
5. The assembled prompt is sent to the OpenAI API (`gpt-4o-mini`).
6. The model's response is parsed and validated against the five-field JSON contract (`answer`, `confidence`, `actions`, `category`, `needs_human`).
7. Usage metrics — token counts, latency, and estimated cost — are calculated and appended as a new row to `metrics/metrics.csv`.
8. The validated JSON is printed to the console.

This design applies a two-gate approach: an input gate (`safety.py`) screens incoming text before it reaches the model, and an output gate (`validar_respuesta`) verifies the model's response before it is delivered. Keeping these checks at the entry and exit points limits the impact of both malicious input and malformed model output.

## 2. Prompt Engineering

The project uses **few-shot prompting**. The prompt file includes worked examples — pairs of a user question and its expected JSON response — so the model learns the exact output shape from concrete cases rather than from an abstract instruction.

Few-shot was chosen deliberately over the alternatives. The core challenge of this project is output-format consistency, not complex reasoning: the assistant must classify a support query and return a stable five-field JSON object every time. Examples teach structure, types and tone more reliably than a description alone. Chain-of-thought was discarded because the task does not require multi-step reasoning, and its intermediate reasoning text would pollute a clean JSON output. Self-consistency was discarded because it runs the model several times per query, multiplying token cost and latency — the very metrics this project is meant to measure and keep low.

Two prompt versions are kept in the repository as evidence of iteration. **v1** is a minimal baseline: a one-line role, the field list, and a single example, with no strict formatting rules. **v2** is the production prompt, organized into three separated blocks — role and hard rules, the JSON schema with exact types, and three few-shot examples covering a high-confidence case, a multi-step case, and a case requiring human escalation. Keeping both versions documents why the production prompt needed more structure than the naive first attempt.

## 3. Metrics — Sample Results

Every successful call records six fields to `metrics/metrics.csv`: timestamp, prompt tokens, completion tokens, total tokens, latency in milliseconds, and estimated cost in USD. Token counts are read directly from the API response; latency is measured with a monotonic timer around the API call; cost is computed from the published `gpt-4o-mini` prices ($0.15 per 1M input tokens, $0.60 per 1M output tokens, verified May 2026).

Across four representative recorded runs:

| Metric | Range observed |
|---|---|
| Prompt tokens | 1,050 – 1,059 |
| Completion tokens | 96 – 164 |
| Total tokens | 1,148 – 1,214 |
| Latency | 3.0 s – 4.8 s |
| Cost per call | $0.000215 – $0.000256 |

The prompt-token count is nearly constant (~1,050) because most of it is the v2 prompt itself — the instructions and the three few-shot examples travel on every call. The completion count varies with the length of the answer. Cost stays a small fraction of a US cent per query.

## 4. Trade-offs and Challenges

**Cost of few-shot.** The few-shot examples add roughly 1,000 fixed tokens to every request. This is an accepted trade-off: the project pays a constant per-call cost in exchange for reliable JSON formatting. For high-volume use, moving the static instructions into a cached system message would reduce this.

**Pattern-based safety is evadable.** The injection detector uses a fixed list of regular expressions. It catches obvious attacks, but a creative attacker can rephrase, translate or encode a payload to bypass it. It is a first line of defense, not a complete solution — combined with the output-contract validation, the system applies two defensive layers rather than one.

**No retry logic.** A transient API error currently stops the script. Production use would need retry with backoff.

**Tests cover validation only.** The five automated tests target the contract validator and never call the API, which keeps them fast and free. Prompt construction and the API call were verified manually; mocked end-to-end tests would close that gap.

## 5. Conclusion

The deliverable is a small, focused pipeline covering the building blocks of a production-shaped LLM feature: a versioned prompt, an enforced output contract, observable cost and latency, automated tests, and a defensive layer against prompt injection. Each piece is intentionally minimal, but the structure is explicit, so any part can be extended without rewriting the rest.