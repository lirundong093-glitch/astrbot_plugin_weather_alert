import asyncio
import json
import os
import uuid
import platform
from datetime import datetime
import aiohttp
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import cairosvg
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image as CompImage

# ---------- 配置键映射 ----------
CONFIG_KEY_API_KEY = "api_key"
CONFIG_KEY_API_HOST = "api_host"
CONFIG_KEY_INTERVAL = "interval"
CONFIG_KEY_CITY = "city"
CONFIG_KEY_TARGET_GROUPS = "target_groups"
CONFIG_KEY_MIN_LEVEL = "min_level"

# ---------- 预警级别映射 ----------
LEVEL_MAP = {
    "red": 1,
    "orange": 2,
    "yellow": 3,
    "blue": 4,
}

@register("weather_alert", "Lucy", "和风天气预警插件", "1.0.0")
class WeatherAlertPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        if config is None:
            config = {}

        # 配置
        self.api_key = config.get(CONFIG_KEY_API_KEY, "")
        self.api_host = config.get(CONFIG_KEY_API_HOST, "").rstrip("/")
        self.interval = int(config.get(CONFIG_KEY_INTERVAL, 30)) * 60  # 转换为秒
        self.city = config.get(CONFIG_KEY_CITY, "")
        self.target_groups = config.get(CONFIG_KEY_TARGET_GROUPS, [])
        self.min_level = int(config.get(CONFIG_KEY_MIN_LEVEL, 4))

        # 存储
        self.latitude = None
        self.longitude = None
        self.seen_alert_ids = set()
        self._current_clear_month = None  # 用于按月清空缓存

        # 图标路径: 插件目录/icons
        self.icons_dir = os.path.join(os.path.dirname(__file__), "icons")

        # 定时任务
        self._task = None
        self._stop_event = asyncio.Event()

        # 会话
        self._session = None

    # ---------- 生命周期 ----------
    async def start(self):
        """插件启用时启动定时任务"""
        if self._task is None:
            self._task = asyncio.ensure_future(self._poll_loop())
            logger.info("[WeatherAlert] 定时任务已启动")

    async def terminate(self):
        """插件停用时取消任务"""
        if self._task:
            self._task.cancel()
            self._task = None
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("[WeatherAlert] 插件已停用")

    # ---------- 每月清空缓存 ----------
    def _check_monthly_clear(self):
        """检测月份是否变化，若变化则清空 seen_alert_ids"""
        now = datetime.now()
        current = (now.year, now.month)
        if self._current_clear_month is None:
            self._current_clear_month = current
        elif self._current_clear_month != current:
            logger.info("[WeatherAlert] 新月份开始，清空已推送预警ID缓存")
            self.seen_alert_ids.clear()
            self._current_clear_month = current

    # ---------- 定时轮询 ----------
    async def _poll_loop(self):
        await asyncio.sleep(5)  # 等待系统就绪
        while True:
            try:
                self._check_monthly_clear()   # 月度清理检查
                logger.info("[WeatherAlert] 开始轮询…")
                await self._fetch_and_process()
            except Exception as e:
                logger.error(f"[WeatherAlert] 轮询异常: {e}")
            await asyncio.sleep(self.interval)

    # ---------- 核心逻辑 ----------
    async def _fetch_and_process(self):
        """获取坐标 -> 获取预警 -> 处理并推送"""
        if not self.api_key or not self.api_host or not self.city:
            logger.warning("[WeatherAlert] 缺少必要配置 (api_key/host/city)")
            return

        if not self._session:
            self._session = aiohttp.ClientSession()

        # 1. 获取城市经纬度
        lat, lon = await self._get_city_coords(self.city)
        if lat is None or lon is None:
            logger.error(f"[WeatherAlert] 无法获取城市 '{self.city}' 的坐标")
            return

        self.latitude = lat
        self.longitude = lon

        # 2. 获取预警信息
        alert_data = await self._get_weather_alert(lat, lon)
        if not alert_data:
            logger.info(f"[WeatherAlert] 当前无预警信息 ({self.city})")
            return

        meta = alert_data.get("metadata", {})
        if meta.get("zeroResult") is True:
            logger.info("[WeatherAlert] zeroResult 为 True，跳过处理")
            return

        alerts = alert_data.get("alerts", [])
        if not alerts:
            logger.info("[WeatherAlert] alerts 为空")
            return

        # 3. 处理每条预警
        for alert in alerts:
            alert_id = alert.get("id")
            if not alert_id:
                continue
            if alert_id in self.seen_alert_ids:
                logger.info(f"[WeatherAlert] 预警 ID {alert_id} 已推送，跳过")
                continue

            # 检查等级
            color_code = alert.get("color", {}).get("code", "").lower()
            level = LEVEL_MAP.get(color_code, 99)
            if level > self.min_level:
                logger.info(f"[WeatherAlert] 预警等级 {color_code} ({level}) 高于阈值 {self.min_level}，跳过")
                continue

            # 生成文本
            text = self._build_alert_text(alert)

            # 生成图片
            img_path = self._generate_alert_image(alert)

            # 推送到每个群聊
            for platform_group in self.target_groups:
                try:
                    if img_path and os.path.exists(img_path):
                        chain = [Plain(text), CompImage.fromFileSystem(img_path)]
                    else:
                        chain = [Plain(text)]
                    await self.context.send_message(platform_group, chain)
                    logger.info(f"[WeatherAlert] 已推送预警 {alert_id} -> {platform_group}")
                except Exception as e:
                    logger.error(f"[WeatherAlert] 推送失败 {platform_group}: {e}")

            self.seen_alert_ids.add(alert_id)

    # ---------- 获取城市坐标 ----------
    async def _get_city_coords(self, city: str):
        """调用 GeoAPI 获取城市经纬度"""
        url = f"https://{self.api_host}/geo/v2/city/lookup"
        params = {"location": city, "key": self.api_key}
        try:
            async with self._session.get(url, params=params, ssl=False) as resp:
                data = await resp.json()
                loc_list = data.get("location", [])
                if loc_list:
                    best = loc_list[0]
                    return best.get("lat"), best.get("lon")
        except Exception as e:
            logger.error(f"[WeatherAlert] GeoAPI 请求失败: {e}")
        return None, None

    # ---------- 获取预警信息 ----------
    async def _get_weather_alert(self, lat, lon):
        """调用 Weather Alert API v1"""
        url = f"https://{self.api_host}/weatheralert/v1/current/{lat}/{lon}"
        headers = {"X-QW-Api-Key": self.api_key}
        try:
            async with self._session.get(url, headers=headers, ssl=False) as resp:
                return await resp.json()
        except Exception as e:
            logger.error(f"[WeatherAlert] Alert API 请求失败: {e}")
        return None

    # ---------- 构造推送文本 ----------
    def _build_alert_text(self, alert: dict) -> str:
        desc = alert.get("description", "")
        inst = alert.get("instruction", "")
        resps = alert.get("responseTypes", [])
        resp_str = "、".join(resps) if resps else "无特定响应类型"
        event_name = alert.get("eventType", {}).get("name", "")
        return f"⚠️ 预警类型: {event_name}\n📌 描述: {desc}\n🛡️ 指引: {inst}\n📋 响应类型: {resp_str}"

    # ---------- 字体获取 ----------
    def _get_system_font(self) -> str:
        system = platform.system()
        font_paths = []
        if system == "Windows":
            font_dir = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
            font_paths = [
                os.path.join(font_dir, "msyh.ttc"),
                os.path.join(font_dir, "simhei.ttf"),
                os.path.join(font_dir, "simsun.ttc"),
            ]
        elif system == "Darwin":
            font_paths = [
                "/System/Library/Fonts/PingFang.ttc",
                "/System/Library/Fonts/STHeiti Light.ttc",
                "/Library/Fonts/Arial Unicode.ttf",
            ]
        else:
            font_paths = [
                "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
                "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
                "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            ]
        for path in font_paths:
            if os.path.exists(path):
                logger.info(f"[WeatherAlert] 使用字体: {path}")
                return path
        return ""

    # ---------- 生成预警图片 ----------
    def _generate_alert_image(self, alert: dict) -> str:
        """根据预警信息生成图片，返回临时文件路径"""
        try:
            color_info = alert.get("color", {})
            r, g, b = color_info.get("red", 255), color_info.get("green", 255), color_info.get("blue", 255)
            a = color_info.get("alpha", 1)
            if isinstance(a, float) and a <= 1:
                a = int(a * 255)
            bg_color = (r, g, b, a)
        except Exception:
            bg_color = (0, 0, 0, 255)

        # 事件名称
        event_name = alert.get("eventType", {}).get("name", "未知预警")
        # 拆分中文逗号/空格 -> 竖排多列
        parts = event_name.replace("，", ",").split(",")
        # 进一步按空格拆分
        text_parts = []
        for p in parts:
            text_parts.extend(p.strip().split())

        # 图片尺寸
        width = 600
        height = 400
        img = Image.new("RGBA", (width, height), bg_color)
        draw = ImageDraw.Draw(img)

        # 左侧 2/3 绘制 SVG 图标
        icon_code = alert.get("icon", "")
        svg_path = os.path.join(self.icons_dir, f"{icon_code}.svg")
        left_width = int(width * 2 / 3)
        if icon_code and os.path.exists(svg_path):
            try:
                png_bytes = cairosvg.svg2png(url=svg_path, output_width=left_width - 20, output_height=height - 20)
                icon_pil = Image.open(BytesIO(png_bytes)).convert("RGBA")
                img.paste(icon_pil, (10, 10), icon_pil)
            except Exception as e:
                logger.warning(f"[WeatherAlert] SVG 渲染失败: {e}")

        # 绘制竖实线
        line_x = left_width + 5
        draw.line([(line_x, 20), (line_x, height - 20)], fill=(255, 255, 255, 255), width=3)

        # 右侧 1/3 绘制竖排文字
        right_area_x = line_x + 15
        right_area_end = width - 10
        font_path = self._get_system_font()
        font_size = 32

        if font_path:
            try:
                font = ImageFont.truetype(font_path, font_size)
            except Exception:
                font = ImageFont.load_default()
        else:
            font = ImageFont.load_default()

        # 计算竖排每列位置
        current_x = right_area_x
        y_start = 20
        for part in text_parts:
            if not part:
                continue
            y = y_start
            for char in part:
                # 避免超出
                if current_x > right_area_end:
                    break
                draw.text((current_x, y), char, fill=(255, 255, 255, 255), font=font)
                y += font_size + 4  # 竖排字间距
            current_x += font_size + 10  # 列间距
            if current_x > right_area_end:
                break

        # 保存临时文件
        tmp_dir = os.path.join(os.path.dirname(__file__), "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, f"alert_{uuid.uuid4().hex}.png")
        img.save(tmp_path)
        return tmp_path

    # ---------- 测试指令 ----------
    @filter.command("weather_alert_test")
    async def test_alert(self, event: AstrMessageEvent):
        yield event.plain_result("⚡ 正在测试天气预警拉取…")
        try:
            await self._fetch_and_process()
            yield event.plain_result("✅ 测试完成，若存在符合条件的预警已推送至白名单群聊。")
        except Exception as e:
            yield event.plain_result(f"❌ 测试失败: {e}")
