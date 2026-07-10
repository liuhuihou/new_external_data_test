# API 配置说明

后端读取同目录下的 `api_config.json`。配置文件用于控制“智能报告”阶段调用哪个 API、用哪个模型、怎么鉴权。

## 1. 前端配置

在网页顶部的 API 配置区可以直接填写：

- `请求地址`
- `API Key`
- `Auth Token`
- `模型`
- `超时`
- `Temperature`
- `Max tokens`
- `ccswitch 配置路径`
- `ccswitch Profile`

点“保存配置”后会写回 `api_config.json`。

## 2. 默认：ccswitch

默认配置：

```json
{
  "active": "ccswitch"
}
```

后端会优先读取：

- `./ccswitch.json`
- `./.ccswitch.json`
- `./ccswitch.config.json`
- `~/.ccswitch/config.json`
- `~/.claude/ccswitch.json`
- `~/.claude/ccswitch/config.json`

也可以在 `api_config.json` 中显式指定：

```json
{
  "active": "ccswitch",
  "profiles": {
    "ccswitch": {
      "ccswitch": {
        "enabled": true,
        "config_path": "E:/path/to/ccswitch.json",
        "profile": "your-profile-name"
      }
    }
  }
}
```

支持读取的 ccswitch 环境字段：

- `ANTHROPIC_BASE_URL`
- `ANTHROPIC_API_KEY`
- `ANTHROPIC_AUTH_TOKEN`
- `ANTHROPIC_MODEL`
- `ANTHROPIC_SMALL_FAST_MODEL`
- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

## 3. Anthropic / Claude

```json
{
  "active": "anthropic",
  "profiles": {
    "anthropic": {
      "api_type": "anthropic",
      "base_url": "https://api.anthropic.com",
      "api_key": "你的 API Key",
      "auth_token": "",
      "model": "claude-3-5-sonnet-20241022",
      "anthropic_version": "2023-06-01",
      "temperature": 0.2,
      "max_tokens": 4096,
      "timeout_seconds": 60
    }
  }
}
```

说明：

- `base_url` 会自动拼接成 `/v1/messages`
- `api_key` 会作为 `x-api-key` 请求头发送
- `auth_token` 会作为 `Authorization: Bearer ...` 请求头发送

## 4. OpenAI 兼容接口

```json
{
  "active": "openai_compatible",
  "profiles": {
    "openai_compatible": {
      "api_type": "openai",
      "api_url": "https://api.openai.com/v1/chat/completions",
      "api_key": "你的 API Key",
      "model": "gpt-4.1-mini",
      "temperature": 0.2,
      "timeout_seconds": 60
    }
  }
}
```

说明：

- `api_key` 会作为 `Authorization: Bearer ...` 请求头发送
- 也支持第三方 OpenAI 兼容网关

## 5. 配置字段

- `active`：当前启用的 profile
- `profiles`：profile 集合
- `api_type`：`anthropic` / `openai` / `ccswitch`
- `api_url`：完整请求地址
- `base_url`：基础地址，Anthropic 会自动补 `/v1/messages`
- `api_key`：API Key
- `auth_token`：额外鉴权 token
- `model`：模型名称
- `temperature`：温度
- `max_tokens`：最大输出 token
- `timeout_seconds`：超时秒数
- `custom_headers`：额外请求头

## 6. 安全说明

调用外部 API 时，后端只发送聚合摘要：

- 字段映射
- 样本概况
- 数据质量指标
- 分箱结果
- Cutoff 规则
- IV / KS / AUC / PSI

不会发送客户明细行。
