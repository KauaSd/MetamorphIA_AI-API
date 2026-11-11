import re
from enum import Enum
from typing import Tuple

class StatusDiagnostico(str, Enum):
    CONFIRMADO = "confirmado"
    SUSPEITA = "suspeita"

def classificar_diagnostico(texto: str) -> Tuple[str, StatusDiagnostico]:
    if not texto or not texto.strip():
        return ("Em investigação", StatusDiagnostico.SUSPEITA)

    texto_limpo = texto.strip()
    if re.search(r"\bsuspeita\b", texto_limpo, re.IGNORECASE):
        nome = re.sub(r"(?i)\bsuspeita\s*(de|para|:)?", "", texto_limpo)
        nome = re.sub(r"[()]", "", nome).strip()
        if not nome:
            nome = "Em investigação"
        return (nome, StatusDiagnostico.SUSPEITA)

    return (texto_limpo, StatusDiagnostico.CONFIRMADO)
