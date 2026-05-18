# Pruebas del contrato JSON del asistente.
# Cómo ejecutarlas desde la raíz del proyecto:
#     .venv/bin/pytest -q
# o, si tienes el venv activado:
#     pytest -q
#
# Las pruebas no realizan llamadas a la API de OpenAI: solo ejercitan
# validar_respuesta con diccionarios construidos a mano.

import sys
from pathlib import Path

# Añadimos src/ al sys.path para poder importar run_query como módulo plano
# sin tener que convertir el proyecto en un paquete instalable. La ruta se
# resuelve desde la ubicación del test, no del cwd, para que `pytest` funcione
# tanto desde la raíz como desde tests/.
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from run_query import validar_respuesta  # noqa: E402


def _respuesta_valida_base() -> dict:
    """Fábrica de un dict válido reutilizable como punto de partida en los tests."""
    return {
        "answer": "Tu pedido está en tránsito y llegará en 2 días hábiles.",
        "confidence": 0.85,
        "actions": ["Revisar el número de seguimiento en el correo de confirmación"],
        "category": "envios",
        "needs_human": False,
    }


def test_respuesta_valida():
    # Camino feliz: los 5 campos presentes con los tipos correctos.
    dato = _respuesta_valida_base()
    ok, motivo = validar_respuesta(dato)
    assert ok is True
    assert motivo == ""


def test_campo_faltante():
    # Falta "category": el validador debe rechazarlo e indicar qué falta.
    dato = _respuesta_valida_base()
    del dato["category"]
    ok, motivo = validar_respuesta(dato)
    assert ok is False
    assert "category" in motivo


def test_tipo_incorrecto():
    # confidence llega como string en vez de número: debe ser rechazado.
    dato = _respuesta_valida_base()
    dato["confidence"] = "alta"
    ok, motivo = validar_respuesta(dato)
    assert ok is False
    assert "confidence" in motivo


def test_confidence_fuera_de_rango():
    # confidence = 1.5 es numérico pero excede el rango [0.0, 1.0].
    dato = _respuesta_valida_base()
    dato["confidence"] = 1.5
    ok, motivo = validar_respuesta(dato)
    assert ok is False
    assert "confidence" in motivo
    assert "rango" in motivo


def test_campo_extra():
    # Los 5 campos están bien, pero hay un campo extra "internal_notes":
    # el contrato es cerrado, así que debe ser rechazado.
    dato = _respuesta_valida_base()
    dato["internal_notes"] = "no debería estar acá"
    ok, motivo = validar_respuesta(dato)
    assert ok is False
    assert "internal_notes" in motivo
