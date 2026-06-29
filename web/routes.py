"""分群城市映射的 Web API 路由"""

import json
import logging
import os
from typing import Any

from quart import jsonify, request

logger = logging.getLogger("astrbot")

PLUGIN_NAME = "astrbot_plugin_weather_alert"

_plugin: Any = None


def _get_mapping() -> dict:
    return _plugin.config.get("group_city_mapping", {}) or {}


def _save_mapping(mapping: dict):
    _plugin.config["group_city_mapping"] = mapping
    # Write through to config file
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "..", "config", f"{PLUGIN_NAME}_config.json"
    )
    try:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["group_city_mapping"] = mapping
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[GroupCityAPI] config write failed: {e}")


def register_routes(context: Any, plugin: Any):
    global _plugin
    _plugin = plugin

    async def _list():
        mapping = _get_mapping()
        items = [{"origin": k, "city": v} for k, v in sorted(mapping.items())]
        return jsonify({
            "items": items,
            "default_city": _plugin.config.get("city", ""),
        })

    async def _add():
        body = await request.get_json()
        origin = (body.get("origin") or "").strip()
        city = (body.get("city") or "").strip()

        if not origin:
            return jsonify({"message": "群标识符不能为空", "ok": False})
        if not city:
            return jsonify({"message": "城市名不能为空", "ok": False})

        mapping = dict(_get_mapping())
        mapping[origin] = city
        _save_mapping(mapping)

        logger.warning(f"[GroupCityAPI] set {origin} -> {city}")
        return jsonify({"message": "已保存 " + origin + " -> " + city})

    async def _delete():
        body = await request.get_json()
        origin = (body.get("origin") or "").strip()

        if not origin:
            return jsonify({"message": "群标识符不能为空", "ok": False})

        mapping = dict(_get_mapping())
        if origin not in mapping:
            return jsonify({"message": "未找到 " + origin + " 的映射", "ok": False})

        del mapping[origin]
        _save_mapping(mapping)

        logger.warning(f"[GroupCityAPI] deleted {origin}")
        return jsonify({"message": "已删除 " + origin})

    routes = [
        ("group_cities", _list, ["GET"], "查询所有群城市映射"),
        ("group_cities", _add, ["POST"], "新增/更新群城市映射"),
        ("group_cities/delete", _delete, ["POST"], "删除群城市映射"),
    ]
    for path, handler, methods, desc in routes:
        context.register_web_api(
            f"/{PLUGIN_NAME}/{path}", handler, methods, desc
        )
    logger.warning(f"[GroupCityAPI] registered {len(routes)} routes")
