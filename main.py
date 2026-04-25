import asyncio
import json
import os
import uuid
import platform
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

        # 图标路径: 插件目录/icons
        self.icons_dir = os.path.join(os.path.dirname(__file__), "icons")

        # 定时任务
        self._task = asyncio.ensure_future(self._poll_loop())
        logger.info("[WeatherAlert] 后台任务已从 __init__ 启动")
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

    # ---------- 定时轮询 ----------
    async def _poll_loop(self):
        await asyncio.sleep(5)  # 等待系统就绪
        while True:
            try:
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
        if meta.get("zeroResult") == "true":
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

            # 自定义消息链封装（满足 send_message 需要 .chain 属性的要求）
            class _MsgChain:
                def __init__(self, items):
                    self.chain = items

            # 推送到每个群聊
            for platform_group in self.target_groups:
                try:
                    components = [Plain(text)]
                    if img_path and os.path.exists(img_path):
                        components.append(CompImage.fromFileSystem(img_path))
                    msg = _MsgChain(components)
                    await self.context.send_message(platform_group, msg)
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
        event_name = alert.get("eventType", {}).get("name", "")
        return f"⚠️ 预警类型: {event_name}\n📌 描述: {desc}\n🛡️ 指引: {inst}"

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
        # ---------- 背景色 ----------
        try:
            color_info = alert.get("color", {})
            r = int(color_info.get("red", 255))
            g = int(color_info.get("green", 255))
            b = int(color_info.get("blue", 255))
            a = int(float(color_info.get("alpha", 1)) * 255)
            bg_color = (r, g, b, a)
        except Exception:
            bg_color = (0, 0, 0, 255)

        # ---------- 事件名称与列拆分 ----------
        event_name = alert.get("eventType", {}).get("name", "未知预警")
        # 统一分隔符：中文逗号、英文逗号、空格
        parts = event_name.replace("，", ",").split(",")
        text_columns = []
        for p in parts:
            text_columns.extend(p.strip().split())
        # 过滤空列
        text_columns = [col for col in text_columns if col]

        # ---------- 画布 ----------
        width = 600
        height = 400
        img = Image.new("RGBA", (width, height), bg_color)
        draw = ImageDraw.Draw(img)

        # 左侧 2/3 宽
        left_width = int(width * 2 / 3)
        # 右侧可用宽度（右侧 1/3 减去边距）
        line_x = left_width + 5          # 竖线 x 坐标
        right_margin = 10
        right_usable = width - line_x - right_margin - 15  # 15 为文字区左边距

        # ---------- 左侧 SVG 图标（转为纯白） ----------
        icon_code = alert.get("icon", "")
        svg_path = os.path.join(self.icons_dir, f"{icon_code}.svg")
        if icon_code and os.path.exists(svg_path):
            try:
                icon_size_w = left_width - 20
                icon_size_h = height - 20
                png_bytes = cairosvg.svg2png(
                    url=svg_path, output_width=icon_size_w, output_height=icon_size_h
                )
                icon_pil = Image.open(BytesIO(png_bytes)).convert("RGBA")
                # 将图标颜色替换为纯白色，保留 alpha 通道
                alpha_channel = icon_pil.getchannel('A')
                white_icon = Image.merge('RGBA', (
                    Image.new('L', icon_pil.size, 255),
                    Image.new('L', icon_pil.size, 255),
                    Image.new('L', icon_pil.size, 255),
                    alpha_channel
                ))
                img.paste(white_icon, (10, 10), white_icon)
            except Exception as e:
                logger.warning(f"[WeatherAlert] SVG 图标处理失败: {e}")

        # ---------- 白色竖实线 ----------
        draw.line([(line_x, 20), (line_x, height - 20)], fill=(255, 255, 255, 255), width=3)

        # ---------- 右侧竖排文字 ----------
        if not text_columns:
            # 没有文字列，直接保存
            tmp_dir = os.path.join(os.path.dirname(__file__), "tmp")
            os.makedirs(tmp_dir, exist_ok=True)
            tmp_path = os.path.join(tmp_dir, f"alert_{uuid.uuid4().hex}.png")
            img.save(tmp_path)
            return tmp_path

        # 自适应字号：让文字列总宽度占满右侧可用区域
        col_count = len(text_columns)
        gap_between_cols = 10
        max_font_size = (right_usable - (col_count - 1) * gap_between_cols) / col_count
        font_size = max(12, int(max_font_size))  # 最小 12px 防过小

        # 加载字体
        font_path = self._get_system_font()
        if font_path:
            try:
                font = ImageFont.truetype(font_path, font_size)
            except Exception:
                font = ImageFont.load_default()
        else:
            font = ImageFont.load_default()

        # 右侧区域几何参数
        top_margin = 20
        bottom_margin = 20
        right_area_center_y = top_margin + (height - top_margin - bottom_margin) / 2
        right_start_x = line_x + 15
        right_end_x = width - right_margin
        right_width = right_end_x - right_start_x

        # 各列的 x 中心（等距分散，让整体占满右侧区域）
        if col_count == 1:
            col_centers = [(right_start_x + right_end_x) / 2]
        else:
            step = right_width / (col_count - 1) if col_count > 1 else 0
            col_centers = [right_start_x + i * step for i in range(col_count)]

        # 逐列绘制竖排文字（精确居中）
        for idx, col_chars in enumerate(text_columns):
            x_center = col_centers[idx]
            col_text = "\n".join(col_chars)  # 每个字符一行，实现竖排
            draw.multiline_text(
                (x_center, right_area_center_y),
                col_text,
                fill=(255, 255, 255, 255),
                font=font,
                anchor='mm',      # 包围盒中心对齐坐标
                align='center',
                spacing=4         # 行间距，相当于原竖排的 y 增量
            )

        # ---------- 保存 ----------
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
