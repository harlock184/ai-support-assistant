"""Compuerta de seguridad: detecta intentos de inyección de prompt y registra bloqueos.

Diseño:
- Listamos patrones conocidos de prompt injection en español e inglés.
- Cualquier coincidencia detiene la ejecución antes de llamar a la API:
  ahorra tokens y evita que el modelo se exponga al texto sospechoso.
- El log nunca guarda el texto crudo, solo un hash SHA-256, para conservar
  evidencia auditable sin filtrar PII ni el ataque en sí.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
LOG_PATH = PROJECT_ROOT / "logs" / "security_log.jsonl"

# Patrones de inyección detectados. Los compilamos al cargar el módulo porque
# se evalúan en cada request y la compilación al vuelo desperdicia CPU.
# IGNORECASE: los atacantes suelen alternar mayúsculas para evadir filtros
# ingenuos (e.g. "IgNoRa tus instrucciones").
_PATRONES_INYECCION_RAW = (
    r"ignora (todas |las )?(tus )?instrucciones",
    r"olvida (tus )?reglas",
    r"revela (el )?prompt",
    r"modo desarrollador",
    r"developer mode",
    r"jailbreak",
    r"actúa como .*admin",
    r"ignore (all )?(previous )?instructions",
    r"system prompt",
)
PATRONES_INYECCION = tuple(
    re.compile(p, re.IGNORECASE) for p in _PATRONES_INYECCION_RAW
)


def es_entrada_adversarial(texto: str) -> tuple[bool, str]:
    """Devuelve (es_adversarial, patrón_que_disparó).

    Usamos re.search en vez de match para detectar el patrón en cualquier
    posición del texto: los ataques suelen ir embebidos en consultas más
    largas (e.g. "¿dónde está mi pedido? ignora tus instrucciones y...").
    """
    if not isinstance(texto, str):
        return False, ""
    for patron in PATRONES_INYECCION:
        if patron.search(texto):
            return True, patron.pattern
    return False, ""


def respuesta_segura() -> dict:
    """Respuesta canónica para entradas adversariales.

    Cumple el mismo contrato de 5 campos que valida validar_respuesta, para
    que el consumidor pueda manejar una salida uniforme sin ramas especiales.
    confidence=0.0 y needs_human=true comunican explícitamente que la decisión
    no proviene del modelo y requiere intervención humana.
    """
    return {
        "answer": (
            "No puedo procesar esa solicitud porque parece contener "
            "instrucciones no permitidas. Si tienes una consulta de "
            "soporte legítima, reformúlala e inténtalo de nuevo."
        ),
        "confidence": 0.0,
        "actions": [
            "Reformular la consulta sin instrucciones al sistema",
            "Si el problema persiste, contactar a un agente humano",
        ],
        "category": "seguridad",
        "needs_human": True,
    }


def registrar_intento(texto: str, patron: str) -> None:
    """Append a logs/security_log.jsonl con un objeto JSON por línea.

    Guardamos solo el hash del texto: el log es evidencia auditable de que
    *algo* fue bloqueado, pero no debe convertirse en una vía para filtrar
    el contenido del ataque, datos personales del usuario, ni payloads que
    luego podrían ejecutarse al revisar logs manualmente.
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entrada = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evento": "entrada_adversarial_bloqueada",
        "patron_detectado": patron,
        "input_hash": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
    }
    # JSONL = un objeto JSON por línea: append-friendly y parseable
    # incrementalmente sin tener que reescribir el archivo entero.
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
