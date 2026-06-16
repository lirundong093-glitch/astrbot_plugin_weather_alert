<div align="center">

[![Moe Counter](https://count.getloli.com/get/@lirundong093-glitch?theme=moebooru)](https://github.com/lirundong093-glitch/astrbot_plugin_weather_alert)

</div>

# 🚨 和风天气预警推送插件 (astrbot_plugin_weather_alert)

基于 AstrBot 框架与和风天气 API 的天气预警轮询推送插件。定时拉取指定城市的气象预警信息，生成可视化图片并推送至目标群聊。

<p align="center">
  <img src="https://img.shields.io/badge/version-v1.0.0-blue" alt="Version">
  <img src="https://img.shields.io/badge/Python-3.8%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/AstrBot-%E6%8F%92%E4%BB%B6%E6%A1%86%E6%9E%B6-brightgreen" alt="AstrBot">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
</p>

## ✨ 功能

- **定时轮询**：按可配置间隔自动拉取城市预警数据
- **等级过滤**：只推送达到阈值的预警（红/橙/黄/蓝可设）
- **图片生成**：基于预警类型渲染带图标和竖排文字的彩色卡片
- **ID 去重**：已推送的预警不会重复发送
- **坐标缓存**：城市经纬度持久化，减少 GeoAPI 调用

## 🖼️ 示例

<div align="center">

![预警示例](https://github.com/lirundong093-glitch/astrbot_plugin_weather_alert/blob/master/image_to_show.png?raw=true)

</div>

## 📥 安装

将插件目录放入 AstrBot 的 `addons/plugins` 对应位置，重启或热重载即可。

### 依赖

```bash
pip install aiohttp Pillow cairosvg
```

## 🛠️ 配置

| 配置项 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `api_key` | string | — | 和风天气 API Key |
| `api_host` | string | `devapi.qweather.com` | API 域名 |
| `interval` | int | `30` | 轮询间隔（分钟） |
| `city` | string | `北京` | 目标城市 |
| `target_groups` | list | `[]` | 推送群聊白名单 |
| `min_level` | int | `4` | 最低预警级别（1=红, 2=橙, 3=黄, 4=蓝） |

> 获取 API Key：前往 [和风天气控制台](https://console.qweather.com) 注册并创建应用。

## 🤖 指令

| 指令 | 说明 |
| :--- | :--- |
| `/weather_alert_test` | 立即执行一次拉取推送流程（调试用） |

## 🔄 工作流程

1. 插件启动后延迟 5 秒开始首次轮询
2. 每次轮询：坐标缓存命中 → 调用 Alert API → 逐条检查等级与去重 → 生成图片 → 推送
3. 坐标缓存与预警 ID 均持久化至 `data/plugin_data/astrbot_plugin_weather_alert/`

## 📁 项目结构

```
astrbot_plugin_weather_alert/
├── main.py          # 插件主逻辑（轮询、API、图片生成、推送）
├── icons/           # 预警类型 SVG 图标
└── _conf_schema.json
```

## 📝 许可证

[MIT License](LICENSE)

## 🙏 鸣谢

- [和风天气](https://www.qweather.com/) — 天气预警数据
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) — 机器人框架
- [Pillow](https://python-pillow.org/) / [cairosvg](https://cairosvg.org/) — 图片生成

---

<p align="center">Made with ❤️ by <a href="https://github.com/lirundong093-glitch">Lucy</a></p>
