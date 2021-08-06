from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from rest_framework.exceptions import ValidationError

from ee.clickhouse.models.action import format_action_filter
from ee.clickhouse.models.property import parse_prop_clauses
from ee.clickhouse.queries.util import format_ch_timestamp, get_earliest_timestamp
from ee.clickhouse.sql.events import EVENT_JOIN_PERSON_SQL
from posthog.constants import TREND_FILTER_TYPE_ACTIONS, WEEKLY_ACTIVE
from posthog.models.entity import Entity
from posthog.models.filters import Filter

MATH_FUNCTIONS = {
    "sum": "sum",
    "avg": "avg",
    "min": "min",
    "max": "max",
    "median": "quantile(0.50)",
    "p90": "quantile(0.90)",
    "p95": "quantile(0.95)",
    "p99": "quantile(0.99)",
}

entity_index = 0


def process_math(entity: Entity) -> Tuple[str, str, Dict[str, Optional[str]]]:
    global entity_index

    # :KLUDGE: Generate a unique parameter name every time this is called to avoid collisions.
    value = f"toFloat64OrNull(JSONExtractRaw(properties, %(e_{entity_index % 1000}_math)s))"
    params = {f"e_{entity_index % 1000}_math": entity.math_property}
    entity_index += 1

    aggregate_operation = "count(*)"
    join_condition = ""
    if entity.math == "dau":
        join_condition = EVENT_JOIN_PERSON_SQL
        aggregate_operation = "count(DISTINCT person_id)"
    elif entity.math in MATH_FUNCTIONS:
        aggregate_operation = f"{MATH_FUNCTIONS[entity.math]}({value})"
        params["join_property_key"] = entity.math_property

    return aggregate_operation, join_condition, params


def parse_response(stats: Dict, filter: Filter, additional_values: Dict = {}) -> Dict[str, Any]:
    counts = stats[1]
    dates = [
        item.strftime(
            "%Y-%m-%d{}".format(", %H:%M" if filter.interval == "hour" or filter.interval == "minute" else "")
        )
        for item in stats[0]
    ]
    labels = [
        item.strftime(
            "%-d-%b-%Y{}".format(" %H:%M" if filter.interval == "hour" or filter.interval == "minute" else "")
        )
        for item in stats[0]
    ]
    days = [
        item.strftime(
            "%Y-%m-%d{}".format(" %H:%M:%S" if filter.interval == "hour" or filter.interval == "minute" else "")
        )
        for item in stats[0]
    ]
    return {
        "data": [float(c) for c in counts],
        "count": float(sum(counts)),
        "labels": labels,
        "days": days,
        **additional_values,
    }


def get_active_user_params(filter: Filter, entity: Entity, team_id: int) -> Dict[str, Any]:
    params = {}
    params.update({"prev_interval": "7 DAY" if entity.math == WEEKLY_ACTIVE else "30 day"})
    diff = timedelta(days=7) if entity.math == WEEKLY_ACTIVE else timedelta(days=30)
    if filter.date_from:
        params.update(
            {
                "parsed_date_from_prev_range": f"AND timestamp >= '{format_ch_timestamp(filter.date_from - diff, filter)}'"
            }
        )
    else:
        try:
            earliest_date = get_earliest_timestamp(team_id)
        except IndexError:
            raise ValidationError("Active User queries require a lower date bound")
        else:
            params.update(
                {
                    "parsed_date_from_prev_range": f"AND timestamp >= '{format_ch_timestamp(earliest_date - diff, filter)}'"
                }
            )

    return params


def populate_entity_params(entity: Entity, team_id: int, table_name: str = "") -> Tuple[Dict, Dict]:
    params, content_sql_params = {}, {}
    if entity.type == TREND_FILTER_TYPE_ACTIONS:
        action = entity.get_action()
        action_query, action_params = format_action_filter(action, table_name=table_name)
        params.update(action_params)
        content_sql_params = {"entity_query": "AND {action_query}".format(action_query=action_query)}
    else:
        prop_filters, prop_filter_params = parse_prop_clauses(entity.properties, team_id)
        content_sql_params = {"entity_query": f"AND event = %(event)s {prop_filters}"}
        params["event"] = entity.id
        params.update(prop_filter_params)

    return params, content_sql_params


def enumerate_time_range(filter: Filter, seconds_in_interval: int) -> List[str]:
    date_from = filter.date_from
    date_to = filter.date_to
    delta = timedelta(seconds=seconds_in_interval)
    time_range: List[str] = []

    if not date_from or not date_to:
        return time_range

    while date_from <= date_to:
        time_range.append(
            date_from.strftime(
                "%Y-%m-%d{}".format(" %H:%M:%S" if filter.interval == "hour" or filter.interval == "minute" else "")
            )
        )
        date_from += delta
    return time_range
