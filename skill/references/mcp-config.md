# OneAgent MCP 手动配置指引（自动配置失败时用）

安装器通常会打印以下参数并自动注册；若自动注册失败，按本页在 OneAgent 里手动添加（约 1 分钟）。

## 步骤

1. 打开 OneAgent → 设置 → 系统工具 / MCP 管理 → 添加服务器
2. 按下表填写：

| 项 | 值 |
|---|---|
| 传输方式 | http（streamable HTTP / SSE） |
| URL | `http://127.0.0.1:8000/mcp` |
| Header | `Authorization: Bearer <你的口令>` |

3. 口令获取：数据目录（默认 `~/procurement-data`，Windows 为 `%USERPROFILE%\procurement-data`）下的 `接口口令.txt` 或 `config.json` 的 `token` 字段
4. 保存后点「测试连接」，出现 `bom-procurement` 服务器即成功

## 验证

对 OneAgent 说「查一下采购库的统计」，Agent 调用 `stats` 工具返回物料数即打通。

## 常见问题

| 现象 | 处理 |
|---|---|
| 连接被拒 | 采购系统没启动：双击 `start.bat` / `start.command` |
| 401 | 口令粘贴错误（注意 Bearer 后有一个空格） |
| 端口冲突 | 用 `PROC_PORT=8001` 启动，URL 同步改 8001 |

## 换机/重装

数据全在数据目录：复制整个 `procurement-data` 到新机器，重跑 `install.sh` / `install.bat`（口令与数据随目录走）。
