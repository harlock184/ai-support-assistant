"""Flujo principal del asistente de soporte: pregunta -> prompt -> OpenAI -> JSON validado."""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


# Resolvemos rutas relativas al archivo del script (no al cwd) para que el
# comando funcione igual desde cualquier directorio del proyecto.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
PROMPT_PATH = PROJECT_ROOT / "prompts" / "main_prompt_v2.txt"
ENV_PATH = PROJECT_ROOT / ".env"

MODEL = "gpt-4o-mini"
# 0.2 prioriza consistencia sobre creatividad: el contrato JSON debe ser estable.
TEMPERATURE = 0.2
DEFAULT_QUESTION = "¿Dónde está mi pedido? Lo hice hace 5 días."

CAMPOS_REQUERIDOS = ("answer", "confidence", "actions", "category", "needs_human")


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


def llamar_openai(prompt: str, api_key: str) -> str:
    """Envía el prompt al modelo y devuelve el contenido bruto del mensaje."""
    client = OpenAI(api_key=api_key)
    # response_format=json_object obliga al modelo a devolver JSON parseable,
    # lo que reduce errores de formato y nos permite confiar en json.loads.
    respuesta = client.chat.completions.create(
        model=MODEL,
        temperature=TEMPERATURE,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    return respuesta.choices[0].message.content


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


def obtener_pregunta_cli() -> str:
    """Toma la pregunta del primer argumento o usa una pregunta por defecto."""
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        return sys.argv[1]
    return DEFAULT_QUESTION


def main() -> int:
    api_key = cargar_api_key()
    pregunta = obtener_pregunta_cli()

    prompt = construir_prompt(pregunta)
    bruto = llamar_openai(prompt, api_key)

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
