"""分群城市映射的 Web API 路由

映射数据存储在 data/plugin_data/astrbot_plugin_weather_alert/group_city_mapping.json
"""

import json
import logging
import os
from typing import Any

from quart import jsonify, request

logger = logging.getLogger("astrbot")

PLUGIN_NAME = "astrbot_plugin_weather_alert"

_plugin: Any = None
_data_dir: str = ""


def _mapping_path() -> str:
    return os.path.join(_data_dir, "group_city_mapping.json")


def _read_mapping() -> dict:
    path = _mapping_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _write_mapping(mapping: dict):
    path = _mapping_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)


def register_routes(context: Any, plugin: Any):
    global _plugin, _data_dir
    _plugin = plugin
    _data_dir = str(plugin.data_dir)

    async def _list():
        mapping = _read_mapping()
        items = [{"origin": k, "city": v} for k, v in sorted(mapping.items())]
        return jsonify({
            "items": items,
            "default_city": _plugin.city or "",
        })

    async def _add():
        body = await request.get_json()
        origin = (body.get("origin") or "").strip()
        city = (body.get("city") or "").strip()

        if not origin:
            return jsonify({"message": "群标识符不能为空", "ok": False})
        if not city:
            return jsonify({"message": "城市名不能为空", "ok": False})

        mapping = _read_mapping()
        mapping[origin] = city
        _write_mapping(mapping)

        logger.warning(f"[GroupCityAPI] set {origin} -> {city}")
        return jsonify({"message": "已保存 " + origin + " -> " + city})

    async def _delete():
        body = await request.get_json()
        origin = (body.get("origin") or "").strip()

        if not origin:
            return jsonify({"message": "群标识符不能为空", "ok": False})

        mapping = _read_mapping()
        if origin not in mapping:
            return jsonify({"message": "未找到 " + origin + " 的映射", "ok": False})

        del mapping[origin]
        _write_mapping(mapping)

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
