"""Flujo principal del asistente de soporte: pregunta -> prompt -> OpenAI -> JSON validado."""

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from openai import OpenAI

from safety import es_entrada_adversarial, registrar_intento, respuesta_segura


# Resolvemos rutas relativas al archivo del script (no al cwd) para que el
# comando funcione igual desde cualquier directorio del proyecto.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
PROMPT_PATH = PROJECT_ROOT / "prompts" / "main_prompt_v2.txt"
ENV_PATH = PROJECT_ROOT / ".env"
METRICS_PATH = PROJECT_ROOT / "metrics" / "metrics.csv"

MODEL = "gpt-4o-mini"
# 0.2 prioriza consistencia sobre creatividad: el contrato JSON debe ser estable.
TEMPERATURE = 0.2
DEFAULT_QUESTION = "¿Dónde está mi pedido? Lo hice hace 5 días."

CAMPOS_REQUERIDOS = ("answer", "confidence", "actions", "category", "needs_human")

# Precios de gpt-4o-mini verificados en mayo 2026 (USD por token).
# Fuente: pricing oficial de OpenAI. Actualizar aquí si cambian.
PRECIO_INPUT_USD = 0.00000015   # $0.15 por 1M tokens de entrada
PRECIO_OUTPUT_USD = 0.00000060  # $0.60 por 1M tokens de salida

# Orden fijo de columnas del CSV: si se modifica, romperíamos compatibilidad
# con filas históricas. Cualquier columna nueva debe ir al final.
COLUMNAS_METRICAS = (
    "timestamp",
    "tokens_prompt",
    "tokens_completion",
    "total_tokens",
    "latency_ms",
    "estimated_cost_usd",
)


def cargar_api_key() -> str:
    """Carga OPENAI_API_KEY desde .env o aborta con un mensaje accionable."""
    load_dotenv(dotenv_path=ENV_PATH)
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print(
            "ERROR: Falta configurar OPENAI_API_KEY. "
            f"Crea un archivo .env en {PROJECT_ROOT} con la línea: "
            "OPENAI_API_KEY=tu_clave_aqui",
            file=sys.stderr,
        )
        sys.exit(1)
    return api_key


def construir_prompt(pregunta: str) -> str:
    """Lee el template v2 y sustituye el marcador {user_question}."""
    plantilla = PROMPT_PATH.read_text(encoding="utf-8")
    # Sustitución directa en vez de str.format para no chocar con otras llaves
    # que pudieran aparecer en los ejemplos few-shot del prompt.
    return plantilla.replace("{user_question}", pregunta)


def llamar_openai(prompt: str, api_key: str):
    """Envía el prompt y devuelve (respuesta_completa, latency_ms).

    Devolvemos el objeto completo —no solo el texto— porque main() necesita
    también respuesta.usage para registrar métricas de tokens.
    """
    client = OpenAI(api_key=api_key)
    # response_format=json_object obliga al modelo a devolver JSON parseable,
    # lo que reduce errores de formato y nos permite confiar en json.loads.
    # perf_counter() porque mide tiempo monotónico de alta resolución, no se ve
    # afectado por ajustes de reloj del sistema y es ideal para benchmarks.
    inicio = time.perf_counter()
    respuesta = client.chat.completions.create(
        model=MODEL,
        temperature=TEMPERATURE,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    latency_ms = (time.perf_counter() - inicio) * 1000.0
    return respuesta, latency_ms


def parsear_respuesta(texto: str) -> dict | None:
    """Convierte el texto en dict. Devuelve None si el JSON es inválido."""
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        # No relanzamos: la validación posterior reportará el problema con un
        # mensaje uniforme para el usuario final.
        return None


def validar_respuesta(dato) -> tuple[bool, str]:
    """Verifica el contrato de los 5 campos. Devuelve (ok, motivo_del_fallo)."""
    if not isinstance(dato, dict):
        return False, "la respuesta no es un objeto JSON"

    faltantes = [c for c in CAMPOS_REQUERIDOS if c not in dato]
    if faltantes:
        return False, f"faltan campos: {', '.join(faltantes)}"

    extra = [c for c in dato.keys() if c not in CAMPOS_REQUERIDOS]
    if extra:
        return False, f"campos no permitidos: {', '.join(extra)}"

    if not isinstance(dato["answer"], str):
        return False, "answer debe ser string"

    # bool es subclase de int en Python, así que excluimos bool explícitamente
    # para que needs_human=True no pase como confidence válido.
    confidence = dato["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return False, "confidence debe ser un número entre 0.0 y 1.0"
    if not (0.0 <= float(confidence) <= 1.0):
        return False, "confidence fuera del rango [0.0, 1.0]"

    if not isinstance(dato["actions"], list):
        return False, "actions debe ser una lista"
    if not all(isinstance(a, str) for a in dato["actions"]):
        return False, "todos los elementos de actions deben ser string"

    if not isinstance(dato["category"], str):
        return False, "category debe ser string"

    if not isinstance(dato["needs_human"], bool):
        return False, "needs_human debe ser booleano"

    return True, ""


def calcular_metricas(respuesta, latency_ms: float) -> dict:
    """Arma el diccionario de métricas a partir de la respuesta y la latencia."""
    uso = respuesta.usage
    prompt_tokens = uso.prompt_tokens
    completion_tokens = uso.completion_tokens
    total_tokens = uso.total_tokens

    estimated_cost_usd = (
        prompt_tokens * PRECIO_INPUT_USD
        + completion_tokens * PRECIO_OUTPUT_USD
    )

    return {
        # ISO 8601 en UTC para que sea ordenable lexicográficamente y libre de
        # ambigüedades por zona horaria al analizar el CSV más tarde.
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tokens_prompt": prompt_tokens,
        "tokens_completion": completion_tokens,
        "total_tokens": total_tokens,
        "latency_ms": round(latency_ms, 1),
        # 6 decimales: suficiente para distinguir costos de ejecuciones cortas
        # sin arrastrar ruido de coma flotante.
        "estimated_cost_usd": round(estimated_cost_usd, 6),
    }


def guardar_metricas(metricas: dict) -> None:
    """Append-only al CSV. Escribe encabezados solo si el archivo aún no existe."""
    # mkdir por si la carpeta metrics/ no estuviera presente en clones nuevos.
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Capturamos existencia antes de abrir en modo append, porque "a" crea el
    # archivo y nos quedaríamos sin saber si hay que poner encabezados.
    archivo_existe = METRICS_PATH.exists()
    with METRICS_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNAS_METRICAS)
        if not archivo_existe:
            writer.writeheader()
        writer.writerow(metricas)


def obtener_pregunta_cli() -> str:
    """Toma la pregunta del primer argumento o usa una pregunta por defecto."""
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        return sys.argv[1]
    return DEFAULT_QUESTION


def main() -> int:
    api_key = cargar_api_key()
    pregunta = obtener_pregunta_cli()

    # Compuerta de seguridad ANTES de construir el prompt o llamar a la API:
    # bloquear localmente nos ahorra tokens, evita exponer al modelo al texto
    # sospechoso y deja un registro auditable del intento.
    adversarial, patron = es_entrada_adversarial(pregunta)
    if adversarial:
        registrar_intento(pregunta, patron)
        print(json.dumps(respuesta_segura(), indent=2, ensure_ascii=False))
        return 0

    prompt = construir_prompt(pregunta)
    respuesta, latency_ms = llamar_openai(prompt, api_key)
    bruto = respuesta.choices[0].message.content

    dato = parsear_respuesta(bruto)
    if dato is None:
        print("ERROR: el modelo devolvió un JSON inválido.", file=sys.stderr)
        print("Respuesta cruda:", file=sys.stderr)
        print(bruto, file=sys.stderr)
        return 2

    ok, motivo = validar_respuesta(dato)
    if not ok:
        print(f"ERROR de contrato: {motivo}", file=sys.stderr)
        print("Respuesta recibida:", file=sys.stderr)
        print(json.dumps(dato, indent=2, ensure_ascii=False), file=sys.stderr)
        return 3

    print(json.dumps(dato, indent=2, ensure_ascii=False))

    # Las métricas solo se registran cuando la ejecución fue exitosa: así el
    # CSV refleja consumo de tokens útil, no llamadas fallidas que igual
    # cobraríamos pero distorsionan el promedio de costo por respuesta válida.
    metricas = calcular_metricas(respuesta, latency_ms)
    guardar_metricas(metricas)

    print(
        f"\n[métricas] tokens={metricas['total_tokens']} "
        f"(prompt={metricas['tokens_prompt']}, "
        f"completion={metricas['tokens_completion']}) | "
        f"latencia={metricas['latency_ms']} ms | "
        f"costo≈${metricas['estimated_cost_usd']:.6f} USD",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
