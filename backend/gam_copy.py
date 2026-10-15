"""
Cópia de configurações e LICAs entre Line Items do Google Ad Manager.

Autenticação:
    Application Default Credentials, ADC.

No Cloud Run:
    Usa automaticamente a Service Account anexada ao serviço.

Não utiliza:
    service-account.json
    private_key
    GOOGLE_APPLICATION_CREDENTIALS

Regra da cópia:
    Copia configurações operacionais e LICAs.
    Preserva nome, Order, status, datas, Goals e Cost Settings do destino.
"""

from copy import deepcopy
from typing import Any, Dict, List, Optional
import argparse
import json
import os
import sys

import google.auth
from google.auth.transport.requests import Request
from googleads import ad_manager

try:
    from config import (
        NETWORK_CODE,
        APPLICATION_NAME,
        API_VERSION,
        ADMANAGER_SCOPE,
    )
except ImportError:
    # Permite executar também como pacote:
    # python -m backend.app
    from backend.config import (
        NETWORK_CODE,
        APPLICATION_NAME,
        API_VERSION,
        ADMANAGER_SCOPE,
    )


# ============================================================
# CONFIGURAÇÕES DE CÓPIA
# ============================================================

# Somente estes campos serão copiados.
#
# Cost Settings e Goals não aparecem nesta lista e, portanto,
# permanecem exatamente como estão no Line Item de destino.
FIELDS_TO_COPY = [
    # Segmentação completa
    "targeting",

    # Tamanhos e configurações esperadas dos criativos
    "creativePlaceholders",

    # Configurações de entrega
    "deliveryRateType",
    "creativeRotationType",
    "roadblockingType",
    "environmentType",
    "companionDeliveryOption",

    # Frequência
    "frequencyCaps",

    # Inventário
    "allowOverbook",
    "skipInventoryCheck",

    # Previsão de entrega
    "deliveryForecastSource",
]

# Campos explicitamente preservados no destino.
#
# Esta lista também serve para documentação e validação.
PRESERVED_FIELDS = [
    # Identificação
    "id",
    "name",
    "orderId",
    "status",

    # Datas
    "startDateTime",
    "endDateTime",
    "startDateTimeType",
    "unlimitedEndDateTime",

    # Goals
    "primaryGoal",
    "secondaryGoals",

    # Cost Settings
    "costType",
    "costPerUnit",
    "valueCostPerUnit",
    "discountType",
    "discount",

    # Quantidades, orçamento e contratação
    "budget",
    "contractedUnitsBought",

    # Campos calculados e somente leitura
    "stats",
    "deliveryIndicator",
    "reservationStatus",
    "creationDateTime",
    "lastModifiedDateTime",
    "isMissingCreatives",
]


# ============================================================
# AUTENTICAÇÃO ADC PARA A BIBLIOTECA SOAP GOOGLEADS
# ============================================================

class AdManagerADCClient:
    """
    Adaptador entre google-auth ADC e a biblioteca SOAP googleads.

    A biblioteca googleads espera que o objeto OAuth forneça
    CreateHttpHeader(). Este adaptador obtém e renova o token
    via Application Default Credentials.
    """

    def __init__(self, scopes: List[str]) -> None:
        self.credentials, self.project_id = google.auth.default(
            scopes=scopes
        )

        self._request = Request()

    def _ensure_valid_token(self) -> None:
        """
        Renova o token quando ele não existe ou expirou.
        """

        if (
            not self.credentials.valid
            or self.credentials.expired
            or not self.credentials.token
        ):
            self.credentials.refresh(self._request)

    def CreateHttpHeader(self) -> Dict[str, str]:
        """
        Método esperado pelo googleads.AdManagerClient.
        """

        self._ensure_valid_token()

        return {
            "Authorization": (
                f"Bearer {self.credentials.token}"
            )
        }

    def Refresh(self) -> None:
        """
        Compatibilidade adicional com clientes que chamam Refresh().
        """

        self.credentials.refresh(self._request)

    @property
    def access_token(self) -> Optional[str]:
        """
        Expõe o access token para compatibilidade.
        """

        self._ensure_valid_token()
        return self.credentials.token


# ============================================================
# CRIAÇÃO DO CLIENTE
# ============================================================

def get_client() -> ad_manager.AdManagerClient:
    """
    Cria o cliente SOAP do Google Ad Manager usando ADC.

    No Cloud Run, ADC usa automaticamente a Service Account
    configurada no serviço.
    """

    oauth_client = AdManagerADCClient(
        scopes=[ADMANAGER_SCOPE]
    )

    client = ad_manager.AdManagerClient(
        oauth2_client=oauth_client,
        application_name=APPLICATION_NAME,
        network_code=str(NETWORK_CODE),
    )

    return client


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def _get_results(response: Any) -> List[Any]:
    """
    Obtém a lista results de respostas SOAP do tipo dict ou suds.
    """

    if not response:
        return []

    if isinstance(response, dict):
        return response.get("results", []) or []

    return getattr(response, "results", []) or []


def _get_field(
    item: Any,
    field: str,
    default: Any = None,
) -> Any:
    """
    Lê um campo tanto de dict quanto de objeto retornado pelo SOAP.
    """

    if item is None:
        return default

    if isinstance(item, dict):
        return item.get(field, default)

    return getattr(item, field, default)


def _has_field(item: Any, field: str) -> bool:
    """
    Verifica se um objeto SOAP ou dict possui determinado campo.
    """

    if isinstance(item, dict):
        return field in item

    return hasattr(item, field)


def _set_field(
    item: Any,
    field: str,
    value: Any,
) -> None:
    """
    Define um campo em dict ou objeto SOAP.
    """

    if isinstance(item, dict):
        item[field] = value
    else:
        setattr(item, field, value)


def _validate_line_item_id(
    line_item_id: Any,
    field_name: str,
) -> int:
    """
    Valida e normaliza um ID de Line Item.
    """

    try:
        normalized_id = int(line_item_id)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{field_name} deve ser um número inteiro."
        ) from error

    if normalized_id <= 0:
        raise ValueError(
            f"{field_name} deve ser maior que zero."
        )

    return normalized_id


def get_line_item(
    line_item_service: Any,
    line_item_id: int,
) -> Any:
    """
    Busca um Line Item pelo ID.
    """

    statement = (
        ad_manager.StatementBuilder(version=API_VERSION)
        .Where("id = :line_item_id")
        .WithBindVariable(
            "line_item_id",
            line_item_id,
        )
        .Limit(1)
    )

    response = line_item_service.getLineItemsByStatement(
        statement.ToStatement()
    )

    results = _get_results(response)

    if not results:
        raise LookupError(
            f"Line Item {line_item_id} não encontrado "
            f"na rede {NETWORK_CODE}."
        )

    return results[0]


def get_licas(
    lica_service: Any,
    line_item_id: int,
) -> List[Any]:
    """
    Busca todas as LICAs de um Line Item usando paginação.
    """

    statement_builder = (
        ad_manager.StatementBuilder(version=API_VERSION)
        .Where("lineItemId = :line_item_id")
        .WithBindVariable(
            "line_item_id",
            line_item_id,
        )
        .Limit(500)
    )

    licas: List[Any] = []

    while True:
        response = (
            lica_service
            .getLineItemCreativeAssociationsByStatement(
                statement_builder.ToStatement()
            )
        )

        page_results = _get_results(response)

        if not page_results:
            break

        licas.extend(page_results)

        statement_builder.offset += (
            statement_builder.limit
        )

    return licas


def delete_target_licas(
    lica_service: Any,
    target_id: int,
) -> int:
    """
    Remove todas as LICAs existentes no Line Item destino.

    Retorna a quantidade informada pela operação, quando disponível.
    """

    statement = (
        ad_manager.StatementBuilder(version=API_VERSION)
        .Where("lineItemId = :line_item_id")
        .WithBindVariable(
            "line_item_id",
            target_id,
        )
        .ToStatement()
    )

    action = {
        "xsi_type":
            "DeleteLineItemCreativeAssociations"
    }

    result = (
        lica_service
        .performLineItemCreativeAssociationAction(
            action,
            statement,
        )
    )

    num_changes = _get_field(
        result,
        "numChanges",
        0,
    )

    return int(num_changes or 0)


def build_target_lica(
    source_lica: Any,
    target_id: int,
) -> Dict[str, Any]:
    """
    Cria o payload de uma nova LICA para o destino.

    Somente campos apropriados para criação são incluídos.
    Campos somente leitura como id, status e stats não são copiados.
    """

    creative_id = _get_field(
        source_lica,
        "creativeId",
    )

    if not creative_id:
        raise ValueError(
            "A LICA de origem não possui creativeId."
        )

    new_lica: Dict[str, Any] = {
        "lineItemId": target_id,
        "creativeId": creative_id,
    }

    # Datas e sizes da LICA de origem NÃO são copiados.
    # - Sem startDateTime/endDateTime, a nova LICA herda a janela
    #   temporal do Line Item de destino.
    # - Sem sizes, o GAM valida a associação pelo tamanho real do
    #   creative contra os creativePlaceholders já copiados para o destino.
    # Isso preserva integralmente startDateTime/endDateTime do Line Item.
    optional_fields = [
        "manualCreativeRotationWeight",
        "sequentialCreativeRotationIndex",
        "destinationUrl",
    ]

    for field in optional_fields:
        value = _get_field(
            source_lica,
            field,
            None,
        )

        if value is not None:
            new_lica[field] = deepcopy(value)

    return new_lica


def create_licas_in_batches(
    lica_service: Any,
    new_licas: List[Dict[str, Any]],
    batch_size: int = 100,
) -> int:
    """
    Cria LICAs em lotes para evitar requisições muito grandes.
    """

    total_created = 0

    for start in range(
        0,
        len(new_licas),
        batch_size,
    ):
        batch = new_licas[
            start:start + batch_size
        ]

        created = (
            lica_service
            .createLineItemCreativeAssociations(
                batch
            )
        )

        total_created += len(created or [])

    return total_created


# ============================================================
# FUNÇÃO PRINCIPAL
# ============================================================

def copiar_configuracao(
    source_id: Any,
    target_id: Any,
) -> Dict[str, Any]:
    """
    Copia configurações operacionais e LICAs do Line Item origem
    para um Line Item destino já existente.

    Preserva no destino:
        - ID
        - nome
        - Order
        - status
        - datas
        - Goals
        - Cost Settings
        - orçamento e quantidade contratada

    Substitui no destino:
        - targeting
        - creativePlaceholders
        - frequência
        - pacing e rotação
        - roadblocking
        - demais campos definidos em FIELDS_TO_COPY
        - todas as LICAs

    Atenção:
        As LICAs atuais do destino são excluídas antes da criação
        das LICAs copiadas da origem.
    """

    source_id = _validate_line_item_id(
        source_id,
        "ID do Line Item de origem",
    )

    target_id = _validate_line_item_id(
        target_id,
        "ID do Line Item de destino",
    )

    if source_id == target_id:
        raise ValueError(
            "O Line Item de origem e o de destino "
            "não podem ser iguais."
        )

    client = get_client()

    line_item_service = client.GetService(
        "LineItemService",
        version=API_VERSION,
    )

    lica_service = client.GetService(
        "LineItemCreativeAssociationService",
        version=API_VERSION,
    )

    # --------------------------------------------------------
    # Buscar origem e destino
    # --------------------------------------------------------

    source = get_line_item(
        line_item_service,
        source_id,
    )

    target = get_line_item(
        line_item_service,
        target_id,
    )

    source_name = _get_field(
        source,
        "name",
        str(source_id),
    )

    target_name = _get_field(
        target,
        "name",
        str(target_id),
    )

    # --------------------------------------------------------
    # Backup em memória dos campos que devem permanecer
    # --------------------------------------------------------

    preserved_values: Dict[str, Any] = {}

    for field in PRESERVED_FIELDS:
        if _has_field(target, field):
            preserved_values[field] = deepcopy(
                _get_field(target, field)
            )

    # --------------------------------------------------------
    # Copiar somente configurações autorizadas
    # --------------------------------------------------------

    copied_fields: List[str] = []

    for field in FIELDS_TO_COPY:
        if _has_field(source, field):
            source_value = _get_field(
                source,
                field,
            )

            _set_field(
                target,
                field,
                deepcopy(source_value),
            )

            copied_fields.append(field)

    # Reaplica os campos protegidos antes da atualização.
    # É uma proteção adicional contra alterações acidentais.
    for field, value in preserved_values.items():
        _set_field(
            target,
            field,
            deepcopy(value),
        )

    # --------------------------------------------------------------
    # Atualizar o Line Item destino
    # --------------------------------------------------------

    updated_items = (
        line_item_service
        .updateLineItems([target])
    )

    if not updated_items:
        raise RuntimeError(
            "O Google Ad Manager não retornou o Line Item "
            "após a atualização."
        )

    # --------------------------------------------------------
    # Consultar LICAs antes de modificar o destino
    # --------------------------------------------------------

    source_licas = get_licas(
        lica_service,
        source_id,
    )

    target_licas = get_licas(
        lica_service,
        target_id,
    )

    source_lica_count = len(source_licas)
    previous_target_lica_count = len(target_licas)

    # --------------------------------------------------------
    # Preparar as LICAs antes de apagar as atuais
    # --------------------------------------------------------

    new_licas = [
        build_target_lica(
            source_lica,
            target_id,
        )
        for source_lica in source_licas
    ]

    # --------------    # --------------------------------------------------------
    # Substituir as LICAs no destino
    # --------------------------------------------------------

    deleted_lica_count = 0
    created_lica_count = 0

    if target_licas:
        deleted_lica_count = delete_target_licas(
            lica_service,
            target_id,
        )

    if new_licas:
        created_lica_count = create_licas_in_batches(
            lica_service,
            new_licas,
        )

    # --------------------------------------------------------
    # Validar o resultado
    # --------------------------------------------------------

    final_target_licas = get_licas(
        lica_service,
        target_id,
    )

    final_target_lica_count = len(
        final_target_licas
    )

    if (
        source_lica_count
        != final_target_lica_count
    ):
        raise RuntimeError(
            "As configurações do Line Item foram atualizadas, "
            "mas a validação das LICAs encontrou divergência. "
            f"Origem: {source_lica_count}; "
            f"destino após a cópia: "
            f"{final_target_lica_count}."
        )

    return {
        "message": (
            "Configurações e LICAs copiadas com sucesso."
        ),
        "network_code": str(NETWORK_CODE),
        "source": {
            "id": source_id,
            "name": source_name,
            "lica_count": source_lica_count,
        },
        "target": {
            "id": target_id,
            "name": target_name,
            "previous_lica_count":
                previous_target_lica_count,
            "deleted_lica_count":
                deleted_lica_count,
            "created_lica_count":
                created_lica_count,
            "final_lica_count":
                final_target_lica_count,
        },
        "copied_fields": copied_fields,
        "preserved_groups": [
            "Identificação",
            "Order",
            "Status",
            "Datas",
            "Goals",
            "Cost Settings",
            "Budget",
            "Quantidade contratada",
        ],
    }

# ============================================================
# EXECUÇÃO DIRETA E GITHUB ACTIONS
# ============================================================

def _read_id(
    cli_value: Optional[str],
    environment_name: str,
) -> Optional[str]:
    """Prioriza o argumento da linha de comando e depois a variável de ambiente."""
    if cli_value not in (None, ""):
        return cli_value
    value = os.getenv(environment_name)
    return value if value not in (None, "") else None


def main() -> int:
    """
    Permite executar este arquivo diretamente.

    Exemplos:
        python backend/gam_copy.py --source-id 123 --target-id 456

    No GitHub Actions, também aceita:
        GAM_SOURCE_LINE_ITEM_ID
        GAM_TARGET_LINE_ITEM_ID

    A saída final é impressa em JSON e também gravada no resumo
    do GitHub Actions quando GITHUB_STEP_SUMMARY estiver disponível.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Copia configurações operacionais e LICAs entre "
            "Line Items existentes do Google Ad Manager."
        )
    )
    parser.add_argument(
        "--source-id",
        dest="source_id",
        help="ID do Line Item de origem.",
    )
    parser.add_argument(
        "--target-id",
        dest="target_id",
        help="ID do Line Item de destino.",
    )
    args = parser.parse_args()

    source_id = _read_id(
        args.source_id,
        "GAM_SOURCE_LINE_ITEM_ID",
    )
    target_id = _read_id(
        args.target_id,
        "GAM_TARGET_LINE_ITEM_ID",
    )

    if source_id is None or target_id is None:
        parser.error(
            "Informe --source-id e --target-id, ou defina "
            "GAM_SOURCE_LINE_ITEM_ID e GAM_TARGET_LINE_ITEM_ID."
        )

    try:
        result = copiar_configuracao(
            source_id=source_id,
            target_id=target_id,
        )

        output = json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        print(output)

        summary_path = os.getenv("GITHUB_STEP_SUMMARY")
        if summary_path:
            source = result["source"]
            target = result["target"]
            copied_fields = ", ".join(result["copied_fields"])

            summary = (
                "# GAM Line Item Copier\n\n"
                "## Resultado\n\n"
                f"- **Rede:** {result['network_code']}\n"
                f"- **Origem:** {source['id']} - {source['name']}\n"
                f"- **Destino:** {target['id']} - {target['name']}\n"
                f"- **LICAs na origem:** {source['lica_count']}\n"
                f"- **LICAs anteriores no destino:** "
                f"{target['previous_lica_count']}\n"
                f"- **LICAs criadas no destino:** "
                f"{target['created_lica_count']}\n"
                f"- **LICAs finais no destino:** "
                f"{target['final_lica_count']}\n"
                f"- **Campos copiados:** {copied_fields}\n"
            )

            with open(
                summary_path,
                "a",
                encoding="utf-8",
            ) as summary_file:
                summary_file.write(summary)

        return 0

    except Exception as error:
        print(
            f"Erro ao copiar configurações e LICAs: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
