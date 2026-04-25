# 🌦️ 极端天气预警插件 (astrbot_plugin_weather_alert)

<div align="center">

[![Moe Counter](https://count.getloli.com/get/@lirundong093-glitch?theme=moebooru)](https://github.com/lirundong093-glitch/astrbot_plugin_everyday_weatherforecast)

</div>

<p align="center">
  <img src="https://img.shields.io/badge/version-v1.1.0-blue" alt="Version">
  <img src="https://img.shields.io/badge/Python-3.8%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/AstrBot-%E6%8F%92%E4%BB%B6%E6%A1%86%E6%9E%B6-brightgreen" alt="AstrBot">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
</p>

一款基于和风天气数据的 AstrBot 插件，定时轮询指定城市的极端天气预警信息，并根据预警等级自动推送到配置的群聊中。支持生成带图标和竖排文字的预警图片，让提醒更直观。

## ✨ 功能特性

- ⏱️ **自动轮询**：后台定时获取最新预警（默认 30 分钟/次）
- 🚨 **等级过滤**：可按红/橙/黄/蓝四级过滤，仅推送高于阈值的预警
- 🖼️ **图文消息**：预警内容以文字 + 自定义图片（含预警图标、类型竖排文字）推送
- 📡 **多群支持**：可同时推送到多个群聊（支持不同平台）
- 🧪 **测试命令**：提供 `weather_alert_test` 指令，手动触发一次拉取与推送

## 📦 安装

1. 进入 AstrBot 的 `addons` 或自定义插件目录
2. 克隆本仓库：
```
bash

cd astrbot/data/plugins
git clone https://github.com/lirundong093-glitch/astrbot_plugin_weather_alert.git
```
3. 安装 Python 依赖（推荐使用虚拟环境）：
```
bash

pip install aiohttp pillow cairosvg
```

4. 在 AstrBot 的插件管理页面或配置文件中启用本插件。

## ⚙️ 配置

在 AstrBot 的插件配置界面或 `data/cmd_config.json` 中添加以下配置项：

| 配置项 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `api_key` | string | ✅ | 无 | [和风天气开发者](https://dev.qweather.com/) 的 API Key |
| `api_host` | string | ✅ | 无 | API 主机地址（通常为 `devapi.qweather.com`） |
| `city` | string | ✅ | 无 | 监控的城市名称（中文，如 `北京`） |
| `target_groups` | array | ✅ | `[]` | 目标群聊标识列表（平台:群号，格式见_conf_schema.json） |
| `interval` | integer | ❌ | `30` | 轮询间隔（分钟） |
| `min_level` | integer | ❌ | `4` | 最小预警等级（1=红色，2=橙色，3=黄色，4=蓝色），仅推送等级 ≤ 此值的预警 |

示例配置（JSON 格式）：
```
json

{
  "api_key": "your_hefeng_api_key",
  "api_host": "devapi.qweather.com",
  "city": "杭州",
  "target_groups": ["qq:123456789", "wechat:group_id"],
  "interval": 20,
  "min_level": 3
}
```
> **提示**：`target_groups` 的具体值需根据 AstrBot 消息平台适配（QQ、微信、飞书等），请参考 [AstrBot 多平台配置](https://astrbot.app/)。

## 🚀 使用

插件启用后即开始后台轮询，无需手动干预。你也可以通过指令立即测试一次：

## 📁 文件结构
```
astrbot_plugin_weather_alert/
├── main.py # 插件主逻辑
├── metadata.yaml # 插件元信息
├── icons/ # 预警图标 SVG 文件（已打包在仓库里）
└── tmp/ # 临时图片存放目录（自动创建）
```
## 🛠️ 常见问题

### 1. 图片中的中文显示为方框

插件会自动检测系统字体（Windows 使用微软雅黑，macOS 使用苹方，Linux 使用 Noto CJK）。若仍不正常，请确保系统已安装中文字体，或修改 `_get_system_font()` 方法指定绝对路径。

### 2. 无法获取预警信息

- 检查 `api_key` 是否有效，`api_host` 是否正确（不含 `https://`）。
- 确保城市名称与和风天气的地理 API 匹配（如 `朝阳` 可能需省市区完整名称）。
- 查看 AstrBot 日志中的错误详情。

### 3. 如何获取和风天气 API Key？

访问 [和风天气控制台](https://console.qweather.com/)，注册后创建项目，选择 **Web API** 类型，即可获得 key。免费版每日有调用次数限制，本插件轮询间隔不宜过短。

## 📝 许可证

本项目采用 [MIT 许可证](LICENSE)。基于 [AstrBot](https://github.com/Soulter/AstrBot) 开发。

## 🤝 贡献

欢迎提交 Issue 或 Pull Request。若本插件对你有帮助，请点个 ⭐️ Star 支持一下～

## 鸣谢

- [AstrBot Repo](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot Plugin Development Docs (Chinese)](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot Plugin Development Docs (English)](https://docs.astrbot.app/en/dev/star/plugin-new.html)

---

<p align="center">Made with ❤️ by <a href="https://github.com/lirundong093-glitch">Lucy</a></p>
