import asyncio
import json
import os
import uuid
import platform
import aiohttp
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import cairosvg
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image as CompImage

# ---------- 配置键映射 ----------
CONFIG_KEY_API_KEY = "api_key"
CONFIG_KEY_API_HOST = "api_host"
CONFIG_KEY_INTERVAL = "interval"
CONFIG_KEY_CITY = "city"
CONFIG_KEY_TARGET_GROUPS = "target_groups"
CONFIG_KEY_MIN_LEVEL = "min_level"
CONFIG_KEY_SKIP_DISMISSED = "skip_dismissed"

# ---------- 预警级别映射 ----------
LEVEL_MAP = {
    "red": 1,
    "orange": 2,
    "yellow": 3,
    "blue": 4,
}


class WeatherAlertPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        if config is None:
            config = {}

        # 配置
        self.api_key = config.get(CONFIG_KEY_API_KEY, "")
        self.api_host = config.get(CONFIG_KEY_API_HOST, "").rstrip("/")
        self.interval = int(config.get(CONFIG_KEY_INTERVAL, 30)) * 60
        self.city = config.get(CONFIG_KEY_CITY, "")
        self.target_groups = config.get(CONFIG_KEY_TARGET_GROUPS, [])
        self.min_level = int(config.get(CONFIG_KEY_MIN_LEVEL, 4))
        self.skip_dismissed = bool(config.get(CONFIG_KEY_SKIP_DISMISSED, True))

        # 资源路径
        resources_dir = os.path.join(os.path.dirname(__file__), "resources")
        self.icons_dir = os.path.join(resources_dir, "icons")
        self.font_path = os.path.join(resources_dir, "fonts", "qweather-icons.ttf")

        # 持久化目录 → data/plugin_data/
        self.data_dir = os.path.join("data", "plugin_data", "astrbot_plugin_weather_alert")
        os.makedirs(self.data_dir, exist_ok=True)
        self.alert_ids_file = os.path.join(self.data_dir, "alert_ids.json")
        self.coords_file = os.path.join(self.data_dir, "coords.json")

        # 定时任务
        self._task = asyncio.ensure_future(self._poll_loop())
        logger.info("[WeatherAlert] 后台任务已从 __init__ 启动")
        self._session = None

    async def start(self):
        if self._task is None:
            self._task = asyncio.ensure_future(self._poll_loop())
            logger.info("[WeatherAlert] 定时任务已启动")

    async def terminate(self):
        if self._task:
            self._task.cancel()
            self._task = None
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("[WeatherAlert] 插件已停用")

    # ---------- 经纬度缓存 ----------
    def _load_coords_from_file(self):
        if not os.path.exists(self.coords_file):
            return None, None
        try:
            with open(self.coords_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("city") != self.city:
                logger.info("[WeatherAlert] 城市已变更，原缓存失效")
                return None, None
            lat, lon = data.get("lat"), data.get("lon")
            if lat is None or lon is None:
                return None, None
            return float(lat), float(lon)
        except Exception as e:
            logger.warning(f"[WeatherAlert] 读取坐标缓存失败: {e}")
            return None, None

    def _save_coords_to_file(self, lat, lon):
        try:
            with open(self.coords_file, "w", encoding="utf-8") as f:
                json.dump({"city": self.city, "lat": lat, "lon": lon}, f, ensure_ascii=False, indent=2)
            logger.info(f"[WeatherAlert] 坐标已缓存: {self.city} ({lat}, {lon})")
        except Exception as e:
            logger.error(f"[WeatherAlert] 保存坐标缓存失败: {e}")

    # ---------- 预警 ID 持久化 ----------
    def _is_alert_id_seen(self, alert_id: str) -> bool:
        if not os.path.exists(self.alert_ids_file):
            return False
        try:
            with open(self.alert_ids_file, "r", encoding="utf-8") as f:
                return alert_id in json.load(f)
        except Exception as e:
            logger.warning(f"[WeatherAlert] 读取预警ID文件失败: {e}")
            return False

    def _mark_alert_id_as_seen(self, alert_id: str):
        ids = []
        if os.path.exists(self.alert_ids_file):
            try:
                with open(self.alert_ids_file, "r", encoding="utf-8") as f:
                    ids = json.load(f)
            except Exception:
                pass
        if alert_id not in ids:
            ids.append(alert_id)
        try:
            with open(self.alert_ids_file, "w", encoding="utf-8") as f:
                json.dump(ids, f, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[WeatherAlert] 保存预警ID文件失败: {e}")

    # ---------- 定时轮询 ----------
    async def _poll_loop(self):
        await asyncio.sleep(5)
        while True:
            try:
                logger.info("[WeatherAlert] 开始轮询…")
                await self._fetch_and_process()
            except Exception as e:
                logger.error(f"[WeatherAlert] 轮询异常: {e}")
            await asyncio.sleep(self.interval)

    # ---------- 核心逻辑 ----------
    async def _fetch_and_process(self):
        if not self.api_key or not self.api_host or not self.city:
            logger.warning("[WeatherAlert] 缺少必要配置 (api_key/host/city)")
            return

        if not self._session:
            self._session = aiohttp.ClientSession()

        # 1. 获取坐标
        lat, lon = self._load_coords_from_file()
        if lat is None or lon is None:
            lat, lon = await self._get_city_coords(self.city)
            if lat is None or lon is None:
                logger.error(f"[WeatherAlert] 无法获取城市 '{self.city}' 的坐标")
                return
            self._save_coords_to_file(lat, lon)

        # 2. 获取预警
        alert_data = await self._get_weather_alert(lat, lon)
        if not alert_data:
            return

        meta = alert_data.get("metadata", {})
        if meta.get("zeroResult") == "true":
            logger.info("[WeatherAlert] zeroResult 为 True，跳过")
            return

        alerts = alert_data.get("alerts", [])
        if not alerts:
            return

        # 3. 处理每条预警
        for alert in alerts:
            alert_id = alert.get("id")
            if not alert_id or self._is_alert_id_seen(alert_id):
                continue

            # 检测"预警信号解除"，跳过推送但持久化记录
            desc = alert.get("description", "")
            if self.skip_dismissed and ("预警信号解除" in desc or "预警解除" in desc):
                self._mark_alert_id_as_seen(alert_id)
                logger.info(f"[WeatherAlert] 跳过预警解除 (id={alert_id}): {desc[:50]}...")
                continue

            color_code = alert.get("color", {}).get("code", "").lower()
            if LEVEL_MAP.get(color_code, 99) > self.min_level:
                continue

            text = self._build_alert_text(alert)
            img_path = self._generate_alert_image(alert)
            success = await self._push_alert(text, img_path)
            if success:
                self._mark_alert_id_as_seen(alert_id)

    async def _push_alert(self, text: str, img_path: str) -> bool:
        """推送预警到所有目标群，失败返回 False"""
        success_count = 0
        for group in self.target_groups:
            try:
                chain = MessageChain().message(text)
                if img_path and os.path.exists(img_path):
                    chain.file_image(img_path)
                await self.context.send_message(group, chain)
                success_count += 1
                logger.info(f"[WeatherAlert] 已推送 -> {group}")
            except Exception as e:
                logger.error(f"[WeatherAlert] 推送失败 {group}: {e}")
            finally:
                if img_path and os.path.exists(img_path):
                    try:
                        os.remove(img_path)
                    except OSError:
                        pass
        return success_count > 0

    # ---------- API 调用 ----------
    async def _get_city_coords(self, city: str):
        url = f"https://{self.api_host}/geo/v2/city/lookup"
        params = {"location": city, "key": self.api_key}
        try:
            async with self._session.get(url, params=params) as resp:
                data = await resp.json()
                loc_list = data.get("location", [])
                if loc_list:
                    return loc_list[0].get("lat"), loc_list[0].get("lon")
        except Exception as e:
            logger.error(f"[WeatherAlert] GeoAPI 失败: {e}")
        return None, None

    async def _get_weather_alert(self, lat, lon):
        url = f"https://{self.api_host}/weatheralert/v1/current/{lat}/{lon}"
        headers = {"X-QW-Api-Key": self.api_key}
        try:
            async with self._session.get(url, headers=headers) as resp:
                return await resp.json()
        except Exception as e:
            logger.error(f"[WeatherAlert] Alert API 失败: {e}")
        return None

    def _build_alert_text(self, alert: dict) -> str:
        desc = alert.get("description", "")
        event_name = alert.get("eventType", {}).get("name", "")
        return f"⚠️ 预警类型: {event_name}\n📌 描述: {desc}"

    # ---------- 字体 ----------
    def _get_font(self) -> str:
        return self.font_path if os.path.exists(self.font_path) else ""

    # ---------- 图片生成 ----------
    def _generate_alert_image(self, alert: dict) -> str:
        try:
            c = alert.get("color", {})
            bg_color = (int(c.get("red", 255)), int(c.get("green", 255)),
                        int(c.get("blue", 255)), int(float(c.get("alpha", 1)) * 255))
        except Exception:
            bg_color = (0, 0, 0, 255)

        event_name = alert.get("eventType", {}).get("name", "未知预警")
        parts = event_name.replace("，", ",").split(",")
        text_columns = [col for p in parts for col in p.strip().split() if col]

        width, height = 600, 400
        img = Image.new("RGBA", (width, height), bg_color)
        draw = ImageDraw.Draw(img)
        left_width = int(width * 2 / 3)
        line_x = left_width + 5

        # 左侧图标
        icon_code = alert.get("icon", "")
        svg_path = os.path.join(self.icons_dir, f"{icon_code}.svg")
        if icon_code and os.path.exists(svg_path):
            try:
                png_bytes = cairosvg.svg2png(url=svg_path, output_width=left_width-20, output_height=height-20)
                icon_pil = Image.open(BytesIO(png_bytes)).convert("RGBA")
                alpha = icon_pil.getchannel('A')
                white = Image.merge('RGBA', (Image.new('L', icon_pil.size, 255),
                     Image.new('L', icon_pil.size, 255), Image.new('L', icon_pil.size, 255), alpha))
                img.paste(white, (10, 10), white)
            except Exception as e:
                logger.warning(f"[WeatherAlert] SVG 图标失败: {e}")

        # 竖线
        draw.line([(line_x, 20), (line_x, height - 20)], fill=(255, 255, 255, 255), width=3)

        if not text_columns:
            tmp_path = os.path.join(self.data_dir, f"alert_{uuid.uuid4().hex}.png")
            img.save(tmp_path)
            return tmp_path

        # 字体与排版
        spacing = 4
        right_start_x, right_end_x = line_x + 15, width - 10
        right_width_col = right_end_x - right_start_x
        max_chars = max((len(col) for col in text_columns), default=0)
        avail_h = height - 40
        font_size_by_h = max(12, int((avail_h - spacing * (max_chars - 1)) / max_chars))
        col_count = len(text_columns)
        gap = 10 if col_count > 1 else 0
        max_w_per_col = (right_width_col - gap * (col_count - 1)) / col_count
        font_size = max(12, min(font_size_by_h, int(max_w_per_col)))

        font_path = self._get_font()
        font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()

        step = right_width_col / (col_count - 1) if col_count > 1 else 0
        col_centers = [right_start_x + i * step for i in range(col_count)] if col_count > 1 else [(right_start_x + right_end_x)/2]
        center_y = height / 2

        for idx, chars in enumerate(text_columns):
            draw.multiline_text((col_centers[idx], center_y), "\n".join(chars),
                                fill=(255,255,255,255), font=font, spacing=spacing, align="center", anchor="mm")

        tmp_path = os.path.join(self.data_dir, f"alert_{uuid.uuid4().hex}.png")
        img.save(tmp_path)
        return tmp_path

    # ---------- 测试指令 ----------
    @filter.command("weather_alert_test")
    async def test_alert(self, event: AstrMessageEvent):
        yield event.plain_result("⚡ 正在测试天气预警拉取…")
        try:
            await self._fetch_and_process()
            yield event.plain_result("✅ 测试完成。")
        except Exception as e:
            yield event.plain_result(f"❌ 测试失败: {e}")
