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

        # 图标路径: 插件目录/icons
        self.icons_dir = os.path.join(os.path.dirname(__file__), "icons")

        # 预警 ID 持久化文件路径
        self.tmp_dir = os.path.join(os.path.dirname(__file__), "tmp")
        os.makedirs(self.tmp_dir, exist_ok=True)
        self.alert_ids_file = os.path.join(self.tmp_dir, "alert_ids.json")
        
        # 经纬度缓存文件
        self.coords_file = os.path.join(self.tmp_dir, "coords.json")
        
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
    
    # ---------- 经纬度缓存（文件） ----------
    def _load_coords_from_file(self):
        """从文件读取坐标，若城市变化或无文件则返回 None"""
        if not os.path.exists(self.coords_file):
            return None, None
        try:
            with open(self.coords_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("city") != self.city:
                logger.info("[WeatherAlert] 城市已变更，原缓存失效")
                return None, None
            lat = data.get("lat")
            lon = data.get("lon")
            if lat is None or lon is None:
                return None, None
            return float(lat), float(lon)
        except Exception as e:
            logger.warning(f"[WeatherAlert] 读取坐标缓存失败: {e}")
            return None, None

    def _save_coords_to_file(self, lat, lon):
        """将坐标与当前城市名写入文件"""
        data = {
            "city": self.city,
            "lat": lat,
            "lon": lon
        }
        try:
            with open(self.coords_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"[WeatherAlert] 坐标已缓存: {self.city} ({lat}, {lon})")
        except Exception as e:
            logger.error(f"[WeatherAlert] 保存坐标缓存失败: {e}")

    # ---------- 存储预警ID ----------
    def _is_alert_id_seen(self, alert_id: str) -> bool:
        """检查文件是否已包含该 ID（纯文件读取）"""
        if not os.path.exists(self.alert_ids_file):
            return False
        try:
            with open(self.alert_ids_file, "r", encoding="utf-8") as f:
                ids = json.load(f)
                return alert_id in ids
        except Exception as e:
            logger.warning(f"[WeatherAlert] 读取预警ID文件失败: {e}")
            return False
    
    def _mark_alert_id_as_seen(self, alert_id: str):
        """将新 ID 追加写入文件（全量更新以保证顺序与去重）"""
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
        lat, lon = self._load_coords_from_file()
        if lat is None or lon is None:
            lat, lon = await self._get_city_coords(self.city)
            if lat is None or lon is None:
                logger.error(f"[WeatherAlert] 无法获取城市 '{self.city}' 的坐标")
                return
            self._save_coords_to_file(lat, lon)

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
            if self._is_alert_id_seen(alert_id):
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
            success_count = 0
            for platform_group in self.target_groups:
                try:
                    components = [Plain(text)]
                    if img_path and os.path.exists(img_path):
                        components.append(CompImage.fromFileSystem(img_path))
                    msg = _MsgChain(components)
                    await self.context.send_message(platform_group, msg)
                    logger.info(f"[WeatherAlert] 已推送预警 {alert_id} -> {platform_group}")
                    success_count += 1
                except Exception as e:
                    logger.error(f"[WeatherAlert] 推送失败 {platform_group}: {e}")
            
            # 删除临时图片（保持不变）
            if img_path and os.path.exists(img_path):
                try:
                    os.remove(img_path)
                    logger.info(f"[WeatherAlert] 已删除临时图片 {img_path}")
                except Exception as e:
                    logger.warning(f"[WeatherAlert] 删除临时图片失败 {img_path}: {e}")
            
            # 仅当至少有一个群推送成功时才标记为已处理
            if success_count > 0:
                self._mark_alert_id_as_seen(alert_id)
                logger.info(f"[WeatherAlert] 预警 {alert_id} 成功推送，ID 已写入文件")

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
        parts = event_name.replace("，", ",").split(",")
        text_columns = []
        for p in parts:
            text_columns.extend(p.strip().split())
        text_columns = [col for col in text_columns if col]

        # ---------- 画布 ----------
        width = 600
        height = 400
        img = Image.new("RGBA", (width, height), bg_color)
        draw = ImageDraw.Draw(img)

        # 左侧 2/3 宽
        left_width = int(width * 2 / 3)
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

        # 右侧竖排文字 ———— 使用 anchor 参数使文字 Y 轴中心对齐画布中心
        spacing = 4
        right_start_x = line_x + 15
        right_end_x = width - 10
        right_width = right_end_x - right_start_x

        if not text_columns:
            tmp_dir = os.path.join(os.path.dirname(__file__), "tmp")
            os.makedirs(tmp_dir, exist_ok=True)
            tmp_path = os.path.join(tmp_dir, f"alert_{uuid.uuid4().hex}.png")
            img.save(tmp_path)
            return tmp_path

        # 计算字体大小（仍然基于安全可用高度，避免超出上下边缘）
        top_margin = 20
        bottom_margin = 20
        available_height = height - top_margin - bottom_margin
        max_chars = max((len(col) for col in text_columns), default=0)
        font_size_by_height = max(12, int((available_height - spacing * (max_chars - 1)) / max_chars))

        col_count = len(text_columns)
        gap_between_cols = 10
        if col_count == 1:
            max_width_per_col = right_width
        else:
            max_width_per_col = (right_width - gap_between_cols * (col_count - 1)) / col_count
        font_size_by_width = int(max_width_per_col)
        font_size = max(12, min(font_size_by_height, font_size_by_width))

        font_path = self._get_system_font()
        if font_path:
            try:
                font = ImageFont.truetype(font_path, font_size)
            except Exception:
                font = ImageFont.load_default()
        else:
            font = ImageFont.load_default()

        # 各列的 x 中心（等距分散，占满右侧横向空间）
        if col_count == 1:
            col_centers = [(right_start_x + right_end_x) / 2]
        else:
            step = right_width / (col_count - 1)
            col_centers = [right_start_x + i * step for i in range(col_count)]

        # 整个画布的 Y 轴中心
        center_y = height / 2

        # 逐列绘制竖排文字，使用 anchor="mm" 自动居中
        for idx, col_chars in enumerate(text_columns):
            x_center = col_centers[idx]
            col_text = "\n".join(col_chars)
            draw.multiline_text(
                (x_center, center_y),      # 锚点位置（列中心 X，画布中心 Y）
                col_text,
                fill=(255, 255, 255, 255),
                font=font,
                spacing=spacing,
                align="center",
                anchor="mm"                # 水平+垂直居中对齐
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
