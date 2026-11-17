from __future__ import annotations

from typing import Any

"""
Regra única de direitos autorais e autorização de mídias (T5 / T6).

Vocabulário oficial de `authorization_status` em `inputs/media_catalog.json`:
- `official_permission`: autorização formal comprovada pelo titular (exige `proof_url` preenchido).
- `licensed`: licença formal verificada (ex.: CC0, Royalty-Free com comprovante, licença paga).
- `fair_use_claimed`: defesa de uso transformativo/editorial invocada pelo criador (não é licença;
  aceita apenas em `editorial` e `non_commercial`, nunca em `commercial`).
- `review_required`: mídia aguardando revisão manual de licença.
- `non_commercial_only`: licença restrita a uso não comercial.
- `restricted`: uso proibido ou com restrição ativa.
- `unknown`: procedência ou direitos desconhecidos (bloqueada em todas as finalidades).

AVISO: O catálogo registra decisões e origem das mídias; não confere autorização legal.
Fair use varia por país e não impede Content ID, reivindicações ou strikes.
"""

VALID_AUTHORIZATION_STATUSES = {
    "official_permission",
    "licensed",
    "fair_use_claimed",
    "review_required",
    "non_commercial_only",
    "restricted",
    "unknown",
}

BLOCKED_COMMERCIAL = {
    "unknown",
    "restricted",
    "review_required",
    "non_commercial_only",
    "fair_use_claimed",
}
BLOCKED_EDITORIAL = {"unknown", "restricted"}


def media_allowed(entry: dict[str, Any] | None, publish_intent: str) -> tuple[bool, str]:
    """
    Avalia se uma mídia pode ser usada para a finalidade informada (`commercial`, `editorial`, `non_commercial`).
    Retorna `(permitido, motivo)`.
    Sem registro no catálogo (`entry is None`) = bloqueado, sempre.
    """
    if not entry:
        return False, "mídia sem registro no catálogo"

    intent = (publish_intent or "commercial").strip().lower()
    status = str(entry.get("authorization_status") or "unknown").strip().lower()

    if intent == "commercial":
        if status in BLOCKED_COMMERCIAL:
            return False, f"status de autorização '{status}'"
        if not entry.get("commercial_use_allowed", False):
            return False, "licença não permite uso comercial"
        return True, ""

    # Política padrão segura para `editorial` e `non_commercial` (bloqueia `unknown` e `restricted`).
    if status in BLOCKED_EDITORIAL:
        return False, f"status de autorização '{status}'"
    return True, ""
