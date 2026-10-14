"""
GAM Line Item Copier
====================

Execucao:
    python backend/gam_copy.py

Variaveis de ambiente:
    GAM_NETWORK_CODE
    GAM_SOURCE_LINE_ITEM_ID
    GAM_DESTINATION_LINE_ITEM_ID

Autenticacao:
    Application Default Credentials (ADC).

No GitHub Actions, as credenciais devem ser disponibilizadas
antes da execucao deste script pela etapa de autenticacao
com Google Cloud / Workload Identity Federation.
"""

import os
import sys
import logging
from typing import Any

import google.auth
from google.auth.transport.requests import AuthorizedSession


# ============================================================
# CONFIGURACAO
# ============================================================

NETWORK_CODE = os.getenv("GAM_NETWORK_CODE", "32352161")

SOURCE_LINE_ITEM_ID = os.getenv("GAM_SOURCE_LINE_ITEM_ID")
DESTINATION_LINE_ITEM_ID = os.getenv("GAM_DESTINATION_LINE_ITEM_ID")

AD_MANAGER_SCOPE = "https://www.googleapis.com/auth/admanager"

API_ROOT = "https://admanager.googleapis.com/v1"


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("gam-copy")


# ============================================================
# CAMPOS QUE PODEM SER COPIADOS
# ============================================================

# Estes campos sao deliberadamente controlados.
#
# Nao copiamos:
# - ID
# - nome
# - order
# - status
# - datas
# - goal
# - cost settings
#
# Dessa maneira o Line Item de destino mantem sua identidade.

FIELDS_TO_COPY = [
    "targeting",
    "creativePlaceholders",
    "environmentType",
    "allowOverbook",
    "skipInventoryCheck",
]


# ============================================================
# AUTENTICACAO
# ============================================================

def get_authenticated_session() -> AuthorizedSession:
    """
    Obtem credenciais usando Application Default Credentials.

    GitHub Actions:
        Workload Identity Federation -> ADC

    Google Cloud:
        Service Account anexada ao workload -> ADC

    Local:
        gcloud auth application-default login
    """

    logger.info("Obtendo credenciais via ADC...")

    credentials, project_id = google.auth.default(
        scopes=[AD_MANAGER_SCOPE]
    )

    if project_id:
        logger.info("Google Cloud Project: %s", project_id)

    credentials.refresh(
        google.auth.transport.requests.Request()
    )

    logger.info("Autenticacao realizada.")

    return AuthorizedSession(credentials)


# ============================================================
# HTTP
# ============================================================

def api_get(
    session: AuthorizedSession,
    resource: str,
) -> dict[str, Any]:

    url = f"{API_ROOT}/{resource}"

    logger.info("GET %s", url)

    response = session.get(url, timeout=60)

    if not response.ok:
        raise RuntimeError(
            "Erro no Ad Manager API.\n"
            f"HTTP: {response.status_code}\n"
            f"Resposta: {response.text}"
        )

    return response.json()


def api_patch(
    session: AuthorizedSession,
    resource: str,
    body: dict[str, Any],
    update_mask: list[str],
) -> dict[str, Any]:

    url = f"{API_ROOT}/{resource}"

    params = {
        "updateMask": ",".join(update_mask)
    }

    logger.info("PATCH %s", url)
    logger.info(
        "Campos que serao atualizados: %s",
        ", ".join(update_mask),
    )

    response = session.patch(
        url,
        params=params,
        json=body,
        timeout=60,
    )

    if not response.ok:
        raise RuntimeError(
            "Erro ao atualizar Line Item.\n"
            f"HTTP: {response.status_code}\n"
            f"Resposta: {response.text}"
        )

    return response.json()


# ============================================================
# LINE ITEMS
# ============================================================

def get_line_item(
    session: AuthorizedSession,
    line_item_id: str,
) -> dict[str, Any]:

    resource = (
        f"networks/{NETWORK_CODE}"
        f"/lineItems/{line_item_id}"
    )

    logger.info(
        "Obtendo Line Item %s...",
        line_item_id,
    )

    line_item = api_get(
        session,
        resource,
    )

    logger.info(
        "Line Item encontrado: %s",
        line_item.get("displayName")
        or line_item.get("name")
        or line_item_id,
    )

    return line_item


# ============================================================
# PREPARACAO
# ============================================================

def prepare_line_item_update(
    source: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:

    body: dict[str, Any] = {}

    update_mask: list[str] = []

    for field in FIELDS_TO_COPY:

        if field not in source:
            logger.warning(
                "Campo '%s' nao existe no Line Item origem.",
                field,
            )
            continue

        body[field] = source[field]
        update_mask.append(field)

    return body, update_mask


# ============================================================
# COPIA
# ============================================================

def copy_line_item_settings(
    session: AuthorizedSession,
    source_id: str,
    destination_id: str,
) -> dict[str, Any]:

    logger.info("=" * 60)
    logger.info("COPIA DE CONFIGURACOES DO LINE ITEM")
    logger.info("=" * 60)

    logger.info(
        "Rede GAM........: %s",
        NETWORK_CODE,
    )

    logger.info(
        "Line Item origem: %s",
        source_id,
    )

    logger.info(
        "Line Item destino: %s",
        destination_id,
    )

    # --------------------------------------------------------
    # ORIGEM
    # --------------------------------------------------------

    source = get_line_item(
        session,
        source_id,
    )

    # --------------------------------------------------------
    # DESTINO
    # --------------------------------------------------------

    destination = get_line_item(
        session,
        destination_id,
    )

    logger.info(
        "Origem : %s",
        source.get("displayName")
        or source.get("name"),
    )

    logger.info(
        "Destino: %s",
        destination.get("displayName")
        or destination.get("name"),
    )

    # --------------------------------------------------------
    # PREPARAR ALTERACOES
    # --------------------------------------------------------

    body, update_mask = prepare_line_item_update(
        source
    )

    if not update_mask:
        raise RuntimeError(
            "Nenhum campo disponivel para copia."
        )

    logger.info("-" * 60)
    logger.info("Campos selecionados para copia:")

    for field in update_mask:
        logger.info("  -> %s", field)

    # --------------------------------------------------------
    # ALTERAR DESTINO
    # -------------------------------------------------------------

    destination_resource = (
        f"networks/{NETWORK_CODE}"
        f"/lineItems/{destination_id}"
    )

    result = api_patch(
        session=session,
        resource=destination_resource,
        body=body,
        update_mask=update_mask,
    )

    logger.info("=" * 60)
    logger.info("Line Item atualizado.")
    logger.info("=" * 60)

    return result


# ============================================================
# ARGUMENTOS
# ============================================================

def get_line_item_ids() -> tuple[str, str\]:

    source = SOURCE_LINE_ITEM_ID
    destination = DESTINATION_LINE_ITEM_ID

    # Tambem permite:
    #
    # python gam_copy.py 123456 987654

    if len(sys.argv) >= 3:
  source = sys.argv[1]
        destination = sys.argv[2]

    if not source:
        raise ValueError(
            "Informe GAM_SOURCE_LINE_ITEM_ID."
        )

    if not destination:
        raise ValueError(
            "Informe GAM_DESTINATION_LINE_ITEM_ID."
        )

    if source == destination:
        raise ValueError(
            "Line Item origem e destino nao podem ser iguais."
        )

    return source, destination


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    try:

        logger.info("")
        logger.info("GAM Line Item Copier")
        logger.info("Rede: %s", NETWORK_CODE)
        logger.info("")

        source_id, destination_id = get_line_item_ids()

        session = get_authenticated_session()

        copy_line_item_settings(
            session=session,
            source_id=source_id,
            destination_id=destination_id,
        )

        logger.info("")
        logger.info("Processo concluido com sucesso.")

    except Exception as exc:

        logger.exception(
            "Falha durante a execucao: %s",
            exc,
        )

        sys.exit(1)


if __name__ == "__main__":
    main()
