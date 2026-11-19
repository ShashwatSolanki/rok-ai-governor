"""Rule-based farm planner built from the latest persisted account state.

Targets are starter heuristics, not game requirements. Upgrade decisions are only
made when the user has supplied a verified cost record; the planner never guesses
building costs or spends resources.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .account_store import AccountStore

DEFAULT_TARGETS = {
    "food": 1_500_000,
    "wood": 1_500_000,
    "stone": 1_000_000,
    "gold": 500_000,
}


def _state_map(store: AccountStore) -> dict[str, Any]:
    return {item["key"]: item["value"] for item in store.latest_state()}


def build_farm_plan(
    database: str | Path = "data/account_history.sqlite3",
    targets: dict[str, int] | None = None,
    upgrade_costs_path: str | Path = "data/upgrade_costs.json",
) -> dict[str, Any]:
    """Build a transparent prioritized plan from known resources and costs."""
    state = _state_map(AccountStore(database))
    targets = targets or DEFAULT_TARGETS
    resources = state.get("resources", {})
    tasks: list[dict[str, Any]] = []
    unknown: list[str] = []

    for resource in ("food", "wood", "stone", "gold"):
        item = resources.get(resource)
        if not isinstance(item, dict) or item.get("value") is None:
            unknown.append(resource)
            continue
        amount = int(item["value"])
        target = int(targets[resource])
        deficit = max(0, target - amount)
        if deficit:
            tasks.append({
                "priority": 1 if resource in ("food", "wood") else 2,
                "type": "gather",
                "resource": resource,
                "current": amount,
                "target": target,
                "shortfall": deficit,
                "instruction": f"Gather {resource} until the reserve reaches {target:,}.",
                "basis": "starter reserve target; adjust after observing actual farm demand",
            })

    cost_path = Path(upgrade_costs_path)
    if cost_path.is_file():
        try:
            upgrades = json.loads(cost_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            upgrades = []
            unknown.append("upgrade_costs_file_invalid")
        if isinstance(upgrades, list):
            available = {
                key: int(value["value"])
                for key, value in resources.items()
                if isinstance(value, dict) and value.get("value") is not None
            }
            for upgrade in upgrades:
                costs = upgrade.get("costs", {})
                if costs and all(available.get(k, -1) >= int(v) for k, v in costs.items()):
                    tasks.append({
                        "priority": int(upgrade.get("priority", 3)),
                        "type": "upgrade_review",
                        "building": upgrade.get("building", "unspecified"),
                        "costs": costs,
                        "instruction": f"Review the {upgrade.get('building', 'listed')} upgrade; all recorded costs appear affordable.",
                        "basis": "user-supplied upgrade cost record; verify in game before starting",
                    })
    else:
        unknown.append("upgrade_costs_not_configured")

    tasks.sort(key=lambda task: (task["priority"], -task.get("shortfall", 0)))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "advisory",
        "account": {
            "power": state.get("power", {}).get("value") if isinstance(state.get("power"), dict) else None,
            "civilization_age": state.get("civilization_age", {}).get("value") if isinstance(state.get("civilization_age"), dict) else None,
        },
        "resources": {
            name: item.get("value")
            for name, item in resources.items()
            if isinstance(item, dict) and item.get("value") is not None
        },
        "tasks": tasks,
        "unknown_or_unconfigured": unknown,
        "notes": [
            "Reserve targets are starter heuristics, not authoritative game advice.",
            "No troop, march, commander, or building availability is inferred unless observed.",
            "Upgrade costs must be recorded from the game; the planner does not invent them.",
        ],
    }


def save_farm_plan(plan: dict[str, Any], path: str | Path = "data/latest_farm_plan.json") -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return destination
