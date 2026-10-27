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
import re
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

    # Creative-level targeting deve viajar junto com os placeholders.
    "creativeTargetings",

    # Configurações de entrega
    "deliveryRateType",
    "creativeRotationType",
    "roadblockingType",
    # environmentType é preservado e validado separadamente.
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
    "environmentType",

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


def _parse_codes(raw: str, label: str) -> List[str]:
    values = [v.strip() for v in re.split(r"[,;\n]+", raw or "") if v.strip()]
    if not values: raise ValueError(f"{label} não pode ficar vazio.")
    invalid = [v for v in values if not re.fullmatch(r"\d{5,6}", v)]
    if invalid: raise ValueError(f"{label}: códigos inválidos: {', '.join(invalid)}")
    return values

def _name_contains_code(name: str, code: str) -> bool:
    return re.search(rf"(?<!\d){re.escape(code)}(?!\d)", name or "") is not None

def find_line_item_candidates_by_name_code(service: Any, code: str) -> List[Any]:
    """Retorna todos os Line Items cujo nome contém o código como token numérico."""
    sb = (
        ad_manager.StatementBuilder(version=API_VERSION)
        .Where("name LIKE :p")
        .WithBindVariable("p", f"%{code}%")
        .Limit(500)
    )
    matches: List[Any] = []
    while True:
        rows = _get_results(service.getLineItemsByStatement(sb.ToStatement()))
        if not rows:
            break
        matches.extend(
            item
            for item in rows
            if _name_contains_code(str(_get_field(item, "name", "")), code)
        )
        sb.offset += sb.limit
    if not matches:
        raise LookupError(
            f"Nenhum Line Item encontrado com o código {code} no nome."
        )
    return matches


def _source_candidate_is_preferred(item: Any) -> bool:
    """Prioriza uma origem não arquivada e não explicitamente inativa."""
    if bool(_get_field(item, "isArchived", False)):
        return False
    status = str(_get_field(item, "status", "") or "").upper()
    return status not in {"INACTIVE", "ARCHIVED", "CANCELED"}


def _get_orders_by_ids(
    order_service: Any,
    order_ids: set[int],
    cache: Dict[int, Any],
) -> None:
    """Carrega Orders ausentes em lotes e os adiciona ao cache."""
    missing = sorted(order_id for order_id in order_ids if order_id not in cache)
    for start in range(0, len(missing), 500):
        batch = missing[start:start + 500]
        if not batch:
            continue
        statement = (
            ad_manager.StatementBuilder(version=API_VERSION)
            .Where("id IN (:order_ids)")
            .WithBindVariable("order_ids", batch)
            .Limit(500)
        )
        response = order_service.getOrdersByStatement(statement.ToStatement())
        for order in _get_results(response):
            cache[int(_get_field(order, "id"))] = order


def _advertiser_id_for_line_item(item: Any, order_cache: Dict[int, Any]) -> int:
    order_id = int(_get_field(item, "orderId"))
    order = order_cache.get(order_id)
    if order is None:
        raise LookupError(f"Order {order_id} não encontrada para o Line Item.")
    return int(_get_field(order, "advertiserId"))


def _candidate_description(item: Any, order_cache: Dict[int, Any]) -> str:
    advertiser_id = _advertiser_id_for_line_item(item, order_cache)
    return (
        f"id={_get_field(item, 'id')}, "
        f"status={_get_field(item, 'status', 'desconhecido')}, "
        f"advertiserId={advertiser_id}, "
        f"nome={_get_field(item, 'name', '')}"
    )


def resolve_pair_by_code(
    line_item_service: Any,
    order_service: Any,
    source_code: str,
    target_code: str,
    order_cache: Dict[int, Any],
) -> Dict[str, Any]:
    """
    Resolve um par com segurança.

    1. Busca todos os candidatos pelos códigos.
    2. Na origem, prioriza candidatos não inativos/arquivados.
    3. Mantém apenas combinações origem-destino do mesmo advertiser.
    4. Exige uma única combinação final.
    """
    sources = find_line_item_candidates_by_name_code(
        line_item_service, source_code
    )
    targets = find_line_item_candidates_by_name_code(
        line_item_service, target_code
    )

    preferred_sources = [item for item in sources if _source_candidate_is_preferred(item)]
    if preferred_sources:
        sources = preferred_sources

    order_ids = {
        int(_get_field(item, "orderId"))
        for item in [*sources, *targets]
    }
    _get_orders_by_ids(order_service, order_ids, order_cache)

    compatible: List[tuple[Any, Any, int]] = []
    for source in sources:
        source_advertiser = _advertiser_id_for_line_item(source, order_cache)
        for target in targets:
            target_advertiser = _advertiser_id_for_line_item(target, order_cache)
            if source_advertiser == target_advertiser:
                compatible.append((source, target, source_advertiser))

    if not compatible:
        source_details = "; ".join(
            _candidate_description(item, order_cache) for item in sources
        )
        target_details = "; ".join(
            _candidate_description(item, order_cache) for item in targets
        )
        raise LookupError(
            f"Nenhuma combinação do mesmo advertiser para {source_code} -> "
            f"{target_code}. Origens: {source_details}. Destinos: {target_details}."
        )

    if len(compatible) != 1:
        details = "; ".join(
            f"origem({_candidate_description(source, order_cache)}) -> "
            f"destino({_candidate_description(target, order_cache)})"
            for source, target, _ in compatible
        )
        raise LookupError(
            f"O par {source_code} -> {target_code} permaneceu ambíguo: "
            f"{len(compatible)} combinações válidas. {details}"
        )

    source, target, advertiser_id = compatible[0]
    source_id = int(_get_field(source, "id"))
    target_id = int(_get_field(target, "id"))
    if source_id == target_id:
        raise ValueError(
            f"O par {source_code} -> {target_code} resolveu para o mesmo "
            f"Line Item {source_id}."
        )
    return {
        "source_code": source_code,
        "target_code": target_code,
        "source_id": source_id,
        "target_id": target_id,
        "advertiser_id": advertiser_id,
        "source_name": str(_get_field(source, "name", source_id)),
        "target_name": str(_get_field(target, "name", target_id)),
        "source_item": source,
        "target_item": target,
    }


def _date_time_parts(value: Any) -> Optional[tuple[int, int, int, int, int, int]]:
    """Normaliza DateTime SOAP/dict para comparação cronológica."""
    if value is None:
        return None
    date = _get_field(value, "date", None)
    if date is None:
        return None
    try:
        return (
            int(_get_field(date, "year")),
            int(_get_field(date, "month")),
            int(_get_field(date, "day")),
            int(_get_field(value, "hour", 0) or 0),
            int(_get_field(value, "minute", 0) or 0),
            int(_get_field(value, "second", 0) or 0),
        )
    except (TypeError, ValueError):
        return None


def _format_date_time(value: Any) -> str:
    parts = _date_time_parts(value)
    if not parts:
        return "data indisponível"
    year, month, day, hour, minute, _ = parts
    return f"{day:02d}/{month:02d}/{year:04d} {hour:02d}:{minute:02d}"


def _pair_direction_warning(pair: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    source = pair.get("source_item")
    target = pair.get("target_item")
    source_start = _get_field(source, "startDateTime", None)
    target_start = _get_field(target, "startDateTime", None)
    source_key = _date_time_parts(source_start)
    target_key = _date_time_parts(target_start)
    if source_key and target_key and source_key > target_key:
        return {
            "source_code": pair["source_code"],
            "target_code": pair["target_code"],
            "source_name": pair["source_name"],
            "target_name": pair["target_name"],
            "source_start": _format_date_time(source_start),
            "target_start": _format_date_time(target_start),
        }
    return None


def check_batch_direction(
    source_raw: str,
    target_raw: str,
    allow_inverted: bool = False,
) -> Dict[str, Any]:
    """Resolve todos os pares e bloqueia possível inversão antes da cópia."""
    sources = _parse_codes(source_raw, "Origens")
    targets = _parse_codes(target_raw, "Destinos")
    if len(sources) != len(targets):
        raise ValueError(
            f"Quantidades diferentes. Origens: {len(sources)}; destinos: {len(targets)}."
        )
    client = get_client()
    line_item_service = client.GetService("LineItemService", version=API_VERSION)
    order_service = client.GetService("OrderService", version=API_VERSION)
    order_cache: Dict[int, Any] = {}
    warnings: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for source_code, target_code in zip(sources, targets):
        try:
            pair = resolve_pair_by_code(
                line_item_service,
                order_service,
                source_code,
                target_code,
                order_cache,
            )
            warning = _pair_direction_warning(pair)
            if warning:
                warnings.append(warning)
        except Exception as error:
            failures.append({
                "source_code": source_code,
                "target_code": target_code,
                "error": str(error),
            })
    result = {
        "ok": not failures and (allow_inverted or not warnings),
        "requires_confirmation": bool(warnings) and not allow_inverted,
        "warnings": warnings,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if failures:
        raise ValueError(
            "A pré-validação encontrou pares que não puderam ser resolvidos: "
            + " | ".join(
                f"{item['source_code']} -> {item['target_code']}: {item['error']}"
                for item in failures
            )
        )
    if warnings and not allow_inverted:
        details = " | ".join(
            f"{item['source_code']} -> {item['target_code']}: origem "
            f"{item['source_start']}; destino {item['target_start']}"
            for item in warnings
        )
        raise RuntimeError(
            "POSSIVEL_INVERSAO: a origem começa depois do destino. " + details
        )
    return result


def _write_batch_summary(result: Dict[str, Any]) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as summary:
        summary.write("\n## Resultado do processamento\n\n")
        summary.write(f"- **Pares informados:** {result['pair_count']}\n")
        summary.write(f"- **Executados com sucesso:** {result['success_count']}\n")
        summary.write(f"- **Não executados:** {result['failure_count']}\n\n")
        if result["successes"]:
            summary.write("### Sucessos\n\n")
            for item in result["successes"]:
                pair = item["pair"]
                summary.write(
                    f"- ✅ `{pair['source_code']} -> {pair['target_code']}` "
                    f"(advertiser `{pair['advertiser_id']}`)\n"
                )
        if result["failures"]:
            summary.write("\n### Não executados\n\n")
            for item in result["failures"]:
                summary.write(
                    f"- ❌ `{item['source_code']} -> {item['target_code']}`: "
                    f"{item['error']}\n"
                )


def copiar_lote_por_codigo(
    source_raw: str,
    target_raw: str,
    allow_inverted: bool = False,
) -> Dict[str, Any]:
    """
    Processa cada par de forma independente.

    Uma falha de resolução ou cópia em um par não impede a execução dos
    demais. Cada copiar_configuracao mantém seu rollback individual.
    """
    sources = _parse_codes(source_raw, "Origens")
    targets = _parse_codes(target_raw, "Destinos")
    if len(sources) != len(targets):
        raise ValueError(
            f"Quantidades diferentes. Origens: {len(sources)}; "
            f"destinos: {len(targets)}."
        )

    client = get_client()
    line_item_service = client.GetService(
        "LineItemService", version=API_VERSION
    )
    order_service = client.GetService(
        "OrderService", version=API_VERSION
    )
    order_cache: Dict[int, Any] = {}
    successes: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    for index, (source_code, target_code) in enumerate(
        zip(sources, targets), start=1
    ):
        print(
            f"[{index}/{len(sources)}] Resolvendo {source_code} -> "
            f"{target_code}",
            flush=True,
        )
        try:
            pair = resolve_pair_by_code(
                line_item_service,
                order_service,
                source_code,
                target_code,
                order_cache,
            )
            direction_warning = _pair_direction_warning(pair)
            if direction_warning and not allow_inverted:
                raise RuntimeError(
                    "POSSIVEL_INVERSAO: a origem começa depois do destino. "
                    f"Origem {direction_warning['source_start']}; "
                    f"destino {direction_warning['target_start']}."
                )
            print(
                f"[{index}/{len(sources)}] Executando "
                f"{pair['source_id']} -> {pair['target_id']} "
                f"(advertiser {pair['advertiser_id']})",
                flush=True,
            )
            copy_result = copiar_configuracao(
                pair["source_id"], pair["target_id"]
            )
            safe_pair = {k: v for k, v in pair.items() if k not in {"source_item", "target_item"}}
            successes.append({"pair": safe_pair, "result": copy_result})
        except Exception as error:
            failure = {
                "source_code": source_code,
                "target_code": target_code,
                "error": str(error),
            }
            failures.append(failure)
            print(
                f"[{index}/{len(sources)}] NÃO EXECUTADO "
                f"{source_code} -> {target_code}: {error}",
                file=sys.stderr,
                flush=True,
            )

    result = {
        "message": (
            "Lote concluído com sucesso."
            if not failures
            else "Lote concluído com ocorrências."
        ),
        "status": (
            "success"
            if not failures
            else "partial_success" if successes else "failure"
        ),
        "pair_count": len(sources),
        "success_count": len(successes),
        "failure_count": len(failures),
        "successes": successes,
        "failures": failures,
    }
    _write_batch_summary(result)
    return result

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


def get_active_licas(
    lica_service: Any,
    line_item_id: int,
) -> List[Any]:
    """Busca somente associações LICA com status ACTIVE."""
    statement_builder = (
        ad_manager.StatementBuilder(version=API_VERSION)
        .Where("lineItemId = :line_item_id AND status = :status")
        .WithBindVariable("line_item_id", line_item_id)
        .WithBindVariable("status", "ACTIVE")
        .Limit(500)
    )
    licas: List[Any] = []
    while True:
        response = lica_service.getLineItemCreativeAssociationsByStatement(
            statement_builder.ToStatement()
        )
        page_results = _get_results(response)
        if not page_results:
            break
        licas.extend(page_results)
        statement_builder.offset += statement_builder.limit
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


def _size_signature(size: Any) -> Optional[tuple[int, int, bool]]:
    """Normaliza um Size SOAP/dict para comparação."""
    if not size:
        return None
    width = _get_field(size, "width", None)
    height = _get_field(size, "height", None)
    if width is None or height is None:
        return None
    is_aspect_ratio = bool(_get_field(size, "isAspectRatio", False))
    return (int(width), int(height), is_aspect_ratio)


def _allowed_pixel_sizes(line_item: Any) -> set[tuple[int, int, bool]]:
    """Retorna apenas sizes dos placeholders PIXEL do Line Item."""
    allowed: set[tuple[int, int, bool]] = set()
    placeholders = _get_field(line_item, "creativePlaceholders", []) or []
    for placeholder in placeholders:
        creative_size_type = _get_field(
            placeholder,
            "creativeSizeType",
            "PIXEL",
        )
        if creative_size_type != "PIXEL":
            continue
        signature = _size_signature(_get_field(placeholder, "size", None))
        if signature:
            allowed.add(signature)
    return allowed


def _filter_lica_sizes_for_target(
    source_lica: Any,
    target_line_item: Any,
) -> Optional[List[Any]]:
    """
    Copia sizes da LICA somente quando forem sizes PIXEL permitidos pelos
    creativePlaceholders do destino. Para Native, não força size na LICA:
    o formato é definido pelo placeholder NATIVE + creativeTemplateId.
    """
    source_sizes = _get_field(source_lica, "sizes", None)
    if not source_sizes:
        return None

    allowed = _allowed_pixel_sizes(target_line_item)
    filtered: List[Any] = []
    for size in source_sizes:
        if _size_signature(size) in allowed:
            filtered.append(deepcopy(size))

    return filtered or None


def get_creative(creative_service: Any, creative_id: int) -> Any:
    """Busca um Creative pelo ID."""
    statement = (
        ad_manager.StatementBuilder(version=API_VERSION)
        .Where("id = :creative_id")
        .WithBindVariable("creative_id", int(creative_id))
        .Limit(1)
    )
    response = creative_service.getCreativesByStatement(statement.ToStatement())
    results = _get_results(response)
    if not results:
        raise LookupError(f"Creative {creative_id} não encontrado.")
    return results[0]


def get_creative_template(creative_template_service: Any, template_id: int) -> Any:
    """Busca um CreativeTemplate pelo ID."""
    statement = (
        ad_manager.StatementBuilder(version=API_VERSION)
        .Where("id = :template_id")
        .WithBindVariable("template_id", int(template_id))
        .Limit(1)
    )
    response = creative_template_service.getCreativeTemplatesByStatement(
        statement.ToStatement()
    )
    results = _get_results(response)
    if not results:
        raise LookupError(f"CreativeTemplate {template_id} não encontrado.")
    return results[0]


def _creative_is_native_by_template(
    creative: Any,
    creative_template_service: Any,
) -> bool:
    """Detecta Native pelo CreativeTemplate, que é a fonte correta da elegibilidade."""
    template_id = _get_field(creative, "creativeTemplateId", None)
    if template_id in (None, ""):
        return False
    template = get_creative_template(
        creative_template_service,
        int(template_id),
    )
    return bool(_get_field(template, "isNativeEligible", False))


def _is_native_creative(creative: Any) -> bool:
    """True somente para TemplateCreative marcado como elegível a Native."""
    return bool(_get_field(creative, "isNativeEligible", False))


def _native_template_id(creative: Any) -> Optional[int]:
    value = _get_field(creative, "creativeTemplateId", None)
    return int(value) if value not in (None, "") else None


def validate_source_creatives_against_placeholders(
    creative_service: Any,
    creative_template_service: Any,
    source_licas: List[Any],
    copied_target: Any,
) -> tuple[Dict[int, Any], set[int]]:
    """Valida LICAs ACTIVE contra os Expected creatives copiados da origem."""
    placeholders = _get_field(copied_target, "creativePlaceholders", []) or []
    pixel_sizes = _allowed_pixel_sizes(copied_target)
    native_template_ids = {
        int(_get_field(ph, "creativeTemplateId"))
        for ph in placeholders
        if _get_field(ph, "creativeSizeType", "PIXEL") == "NATIVE"
        and _get_field(ph, "creativeTemplateId", None) not in (None, "")
    }

    creatives: Dict[int, Any] = {}
    native_creative_ids: set[int] = set()
    errors: List[str] = []

    for index, lica in enumerate(source_licas):
        creative_id = int(_get_field(lica, "creativeId"))
        creative = get_creative(creative_service, creative_id)
        creatives[creative_id] = creative

        is_native = _creative_is_native_by_template(
            creative,
            creative_template_service,
        )
        if is_native:
            native_creative_ids.add(creative_id)
            template_id = _native_template_id(creative)
            if template_id is None or template_id not in native_template_ids:
                errors.append(
                    f"LICA ACTIVE [{index}] Creative {creative_id}: formato Native "
                    f"(template {template_id}) não está em Expected creatives da origem."
                )
            continue

        signature = _size_signature(_get_field(creative, "size", None))
        if signature not in pixel_sizes:
            desc = (
                f"{signature[0]}x{signature[1]}"
                if signature else "desconhecido"
            )
            errors.append(
                f"LICA ACTIVE [{index}] Creative {creative_id}: size {desc} não "
                "está em Expected creatives da origem."
            )

    if errors:
        raise ValueError(
            "Antes de copiar, ajuste Expected creatives na linha de origem: "
            + " | ".join(errors)
        )

    return creatives, native_creative_ids


def validate_creative_targeting_references(
    line_item: Any,
    licas: List[Any],
) -> None:
    """Valida CreativeTargeting.name, placeholder.targetingName e LICA.targetingName."""
    creative_targetings = _get_field(line_item, "creativeTargetings", []) or []
    names = {
        str(_get_field(item, "name"))
        for item in creative_targetings
        if _get_field(item, "name", None) not in (None, "")
    }
    referenced: set[str] = set()
    for placeholder in (_get_field(line_item, "creativePlaceholders", []) or []):
        name = _get_field(placeholder, "targetingName", None)
        if name not in (None, ""):
            referenced.add(str(name))
    for lica in licas:
        name = _get_field(lica, "targetingName", None)
        if name not in (None, ""):
            referenced.add(str(name))
    missing = sorted(referenced - names)
    if missing:
        raise ValueError(
            "Creative Targeting inconsistente na origem. Referências sem "
            f"CreativeTargeting correspondente: {missing}."
        )


def build_target_lica(
    source_lica: Any,
    target_id: int,
    target_line_item: Optional[Any] = None,
    creative: Optional[Any] = None,
    is_native: bool = False,
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

    # Para Native, não envia sizes na LICA. O formato Native é validado
    # pelo creativeTemplateId do Creative contra o placeholder NATIVE.
    # Para Pixel, envia somente sizes explicitamente compatíveis.
    if target_line_item is not None and not is_native:
        compatible_sizes = _filter_lica_sizes_for_target(
            source_lica,
            target_line_item,
        )
        if compatible_sizes:
            new_lica["sizes"] = compatible_sizes

    # Datas da LICA de origem NÃO são copiadas.
    # Sem startDateTime/endDateTime, a nova LICA usa a janela temporal
    # do Line Item de destino. Sizes PIXEL são mantidos somente quando
    # existem nos creativePlaceholders PIXEL do destino. Placeholders
    # NATIVE são preservados no próprio Line Item com creativeTemplateId.
    optional_fields = [
        # Deve referenciar CreativeTargeting.name do Line Item destino.
        "targetingName",
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
# ALTERAÇÃO SEGURA / ROLLBACK
# ============================================================

def _is_not_enough_inventory_error(error: Exception) -> bool:
    return "NOT_ENOUGH_INVENTORY" in str(error)


def _statement_for_line_item(line_item_id: int) -> Dict[str, Any]:
    return (
        ad_manager.StatementBuilder(version=API_VERSION)
        .Where("id = :line_item_id")
        .WithBindVariable("line_item_id", line_item_id)
        .ToStatement()
    )


def _reserve_with_overbook(line_item_service: Any, line_item_id: int) -> None:
    line_item_service.performLineItemAction(
        {
            "xsi_type": "ReserveAndOverbookLineItems",
            "skipInventoryCheck": True,
        },
        _statement_for_line_item(line_item_id),
    )


def _restore_licas(
    lica_service: Any,
    target_id: int,
    original_licas: List[Any],
) -> None:
    current = get_licas(lica_service, target_id)
    if current:
        delete_target_licas(lica_service, target_id)

    payloads = [
        build_target_lica(lica, target_id)
        for lica in original_licas
    ]
    if payloads:
        create_licas_in_batches(lica_service, payloads)


def _update_with_inventory_handling(
    line_item_service: Any,
    target: Any,
    target_id: int,
) -> tuple[List[Any], bool]:
    """Atualiza; em NOT_ENOUGH_INVENTORY, libera, atualiza e reserva novamente."""
    try:
        result = line_item_service.updateLineItems([target])
        return list(result or []), False
    except Exception as error:
        if not _is_not_enough_inventory_error(error):
            raise

    statement = _statement_for_line_item(target_id)
    line_item_service.performLineItemAction(
        {"xsi_type": "ReleaseLineItems"},
        statement,
    )

    refreshed = get_line_item(line_item_service, target_id)
    for field in FIELDS_TO_COPY:
        if _has_field(target, field):
            _set_field(refreshed, field, deepcopy(_get_field(target, field)))

    normalize_video_request_platform_targeting(target, refreshed, fallback=refreshed)
    result = line_item_service.updateLineItems([refreshed])
    _reserve_with_overbook(line_item_service, target_id)
    return list(result or []), True


def _environment_label(value: Any) -> str:
    if value == "VIDEO_PLAYER":
        return "VIDEO"
    if value == "BROWSER":
        return "DISPLAY"
    return str(value or "DESCONHECIDO")


def validate_environment_compatibility(source: Any, target: Any) -> None:
    source_environment = _get_field(source, "environmentType", None)
    target_environment = _get_field(target, "environmentType", None)
    if source_environment != target_environment:
        raise ValueError(
            "Tipos de Line Item incompatíveis: "
            f"origem={_environment_label(source_environment)}; "
            f"destino={_environment_label(target_environment)}. "
            "A cópia exige DISPLAY->DISPLAY ou VIDEO->VIDEO."
        )


def _get_request_platform_targeting(line_item: Any) -> Any:
    targeting = _get_field(line_item, "targeting", None)
    return _get_field(targeting, "requestPlatformTargeting", None)


def _set_request_platform_targeting(line_item: Any, value: Any) -> None:
    targeting = _get_field(line_item, "targeting", None)
    if targeting is None:
        raise ValueError("Line Item sem objeto targeting; atualização bloqueada.")
    _set_field(targeting, "requestPlatformTargeting", deepcopy(value))


def _request_platform_description(value: Any) -> str:
    if value is None:
        return "NULL"
    values = _get_field(value, "targetedRequestPlatforms", []) or []
    return str([str(item) for item in values])


def normalize_video_request_platform_targeting(source: Any, target: Any, fallback: Any = None) -> None:
    """Evita requestPlatformTargeting NULL em updates de VIDEO_PLAYER."""
    if _get_field(target, "environmentType", None) != "VIDEO_PLAYER":
        return
    source_value = _get_request_platform_targeting(source)
    target_value = _get_request_platform_targeting(fallback or target)
    if source_value is not None:
        chosen, chosen_from = source_value, "origem"
    elif target_value is not None:
        chosen, chosen_from = target_value, "destino"
    else:
        # A SOAP API pode omitir este objeto mesmo em Line Items de vídeo válidos.
        # Enviamos o objeto explicitamente com lista vazia, em vez de NULL.
        chosen = {"targetedRequestPlatforms": []}
        chosen_from = "fallback explícito (todas as plataformas)"
    _set_request_platform_targeting(target, chosen)
    print(
        "RequestPlatformTargeting VIDEO: "
        f"fonte={chosen_from}; valor={_request_platform_description(chosen)}",
        flush=True,
    )


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
        - LICAs ativas da origem, compartilhando os mesmos creativeIds

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

    creative_service = client.GetService(
        "CreativeService",
        version=API_VERSION,
    )

    creative_template_service = client.GetService(
        "CreativeTemplateService",
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

    # Validação estrutural antes de qualquer alteração.
    validate_environment_compatibility(source, target)

    # Snapshot completo do destino antes de qualquer alteração.
    original_target = deepcopy(target)
    original_target_licas = get_licas(lica_service, target_id)
    original_reservation_status = _get_field(
        target, "reservationStatus", None
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

    # --------------------------------------------------------
    # Atualizar destino e LICAs com rollback em caso de falha
    # --------------------------------------------------------

    reservation_retry_used = False
    deleted_lica_count = 0
    created_lica_count = 0

    # Lê e prepara todas as LICAs ANTES de alterar o destino.
    # Compartilha somente os creatives com associação ACTIVE na origem.
    source_licas = get_active_licas(lica_service, source_id)
    target_licas = original_target_licas
    source_lica_count = len(source_licas)
    previous_target_lica_count = len(target_licas)

    # Valida Creative Targeting completo antes de qualquer alteração.
    validate_creative_targeting_references(target, source_licas)
    normalize_video_request_platform_targeting(source, target, fallback=original_target)

    # Validação prévia: se houver incompatibilidade PIXEL/NATIVE, falha aqui
    # antes de qualquer update/release no Line Item destino.
    source_creatives, native_creative_ids = (
        validate_source_creatives_against_placeholders(
            creative_service,
            creative_template_service,
            source_licas,
            target,
        )
    )

    new_licas = [
        build_target_lica(
            source_lica,
            target_id,
            target,
            source_creatives[int(_get_field(source_lica, "creativeId"))],
            int(_get_field(source_lica, "creativeId")) in native_creative_ids,
        )
        for source_lica in source_licas
    ]

    try:
        updated_items, reservation_retry_used = _update_with_inventory_handling(
            line_item_service,
            target,
            target_id,
        )

        if not updated_items:
            raise RuntimeError(
                "O Google Ad Manager não retornou o Line Item após a atualização."
            )

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

    except Exception as operation_error:
        rollback_errors: List[str] = []

        # 1. Tenta restaurar o Line Item original.
        def _rollback_line_item() -> None:
            rollback_target = deepcopy(original_target)
            rollback_fallback = get_line_item(line_item_service, target_id)
            normalize_video_request_platform_targeting(
                original_target, rollback_target, fallback=rollback_fallback
            )
            line_item_service.updateLineItems([rollback_target])

        try:
            try:
                _rollback_line_item()
            except Exception as rollback_update_error:
                if _is_not_enough_inventory_error(rollback_update_error):
                    statement = _statement_for_line_item(target_id)
                    try:
                        line_item_service.performLineItemAction(
                            {"xsi_type": "ReleaseLineItems"},
                            statement,
                        )
                    except Exception:
                        pass
                    _rollback_line_item()
                else:
                    raise
        except Exception as rollback_error:
            rollback_errors.append(
                f"falha ao restaurar configurações do Line Item: {rollback_error}"
            )

        # 2. Restaura as LICAs originais.
        try:
            _restore_licas(
                lica_service,
                target_id,
                original_target_licas,
            )
        except Exception as rollback_error:
            rollback_errors.append(
                f"falha ao restaurar LICAs: {rollback_error}"
            )

        # 3. Restaura a condição de reserva quando o destino era RESERVED.
        if original_reservation_status == "RESERVED":
            try:
                _reserve_with_overbook(line_item_service, target_id)
            except Exception as rollback_error:
                rollback_errors.append(
                    f"falha ao restaurar reserva: {rollback_error}"
                )

        if rollback_errors:
            raise RuntimeError(
                f"A cópia falhou: {operation_error}. "
                "O rollback também encontrou problemas: "
                + " | ".join(rollback_errors)
            ) from operation_error

        raise RuntimeError(
            f"A cópia falhou e o destino foi restaurado: {operation_error}"
        ) from operation_error

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
        "reservation_retry_used": reservation_retry_used,
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
    parser=argparse.ArgumentParser(description="Copia configurações e LICAs entre Line Items do Google Ad Manager.")
    parser.add_argument("--source-codes"); parser.add_argument("--target-codes")
    parser.add_argument("--check-direction-only", action="store_true")
    parser.add_argument("--source-id",dest="source_id"); parser.add_argument("--target-id",dest="target_id")
    args=parser.parse_args()
    sc=args.source_codes or os.getenv("GAM_SOURCE_CODES"); tc=args.target_codes or os.getenv("GAM_TARGET_CODES")
    sid=_read_id(args.source_id,"GAM_SOURCE_LINE_ITEM_ID"); tid=_read_id(args.target_id,"GAM_TARGET_LINE_ITEM_ID")
    try:
        if sc or tc:
            if not sc or not tc: raise ValueError("Informe origens e destinos.")
            allow_inverted = str(os.getenv("GAM_ALLOW_INVERTED", "false")).lower() == "true"
            if args.check_direction_only:
                check_batch_direction(sc, tc, allow_inverted=allow_inverted)
                return 0
            result=copiar_lote_por_codigo(sc,tc,allow_inverted=allow_inverted)
            if result.get("status") == "failure":
                print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
                return 1
        else:
            if sid is None or tid is None: raise ValueError("Informe GAM_SOURCE_CODES/GAM_TARGET_CODES ou os IDs reais.")
            result=copiar_configuracao(sid,tid)
        print(json.dumps(result,ensure_ascii=False,indent=2,default=str)); return 0
    except Exception as error:
        print(f"Erro ao copiar configurações e LICAs: {error}",file=sys.stderr); return 1

if __name__ == "__main__":
    raise SystemExit(main())
