# bom-procurement 器件采购库

小企业本地采购库 + OneAgent（MCP）双形态工具。

**双击即用的独立软件**：管理物料、历史采购价、别名、单位换算——不需要 OneAgent 也完整可用。
**同一进程的 MCP 服务**：OneAgent 两个 Skill（资料录入 / BOM 比对）经 `http://127.0.0.1:8000/mcp` 读写同一个 SQLite 库，人工在界面改的，Agent 立即可见。

## 三条红线

1. **价格只能来自库内记录**——库里没有就留空、进「未匹配清单」，Agent 绝不估算或编造
2. **每条价格可追溯**——带来源（文件#sheet#行号）与日期
3. **同型号多价并存**——不覆盖历史；人工确认的型号对应写回别名表

## 快速开始

### Windows
从 [Releases](../../releases) 下载 `bom-procurement.exe`，双击运行（SmartScreen 提示时选「更多信息→仍要运行」）。浏览器自动打开操作界面。

### macOS（未签名，自用）

从 [Releases](../../releases) 下载 `bom-procurement`（无后缀）后，因未做 Apple 签名，首次运行会被 Gatekeeper 拦截。放行命令：

```bash
cd ~/Downloads
chmod +x bom-procurement
xattr -d com.apple.quarantine bom-procurement
./bom-procurement
```

> 若系统设置里找不到「仍要打开」入口（macOS 15+ 常见），务必用上面的命令方式。
> 完整说明（四种方式 + 常见报错处理）：**[docs/install-macos.md](docs/install-macos.md)**

也可**源码安装**（不触发任何拦截，推荐）：`git clone` 后 `bash install.sh`，详见 macOS 安装说明。

### 源码运行 / Skill 安装
```bash
git clone https://github.com/xufei8738-max/bom-procurement.git
cd bom-procurement
bash install.sh     # Windows: install.bat（自动建 venv、装依赖、建库、注册开机自启）
```
或下载 Release 里的 `bom-procurement.skill`，对 OneAgent 说「安装采购系统」。

## 支持的资料格式（Skill 1 录入）

| 格式 | 路径 |
|---|---|
| Excel .xlsx/.xls、CSV、Word .docx | 直读表格 |
| PDF（电子版，有文本层） | pymupdf 表格抽取 |
| PDF（扫描件）、图片 png/jpg/bmp/tif/webp | OCR（macOS 用系统 Vision；Windows/Linux 用 RapidOCR） |

录入一律**先预览、人工确认、再入库**；OCR 结果强制提醒复核。

## OneAgent MCP 对接

| 项 | 值 |
|---|---|
| 传输 | http（streamable HTTP） |
| URL | `http://127.0.0.1:8000/mcp` |
| Header | `Authorization: Bearer <口令>` |

口令在数据目录（默认 `~/procurement-data`）：`接口口令.txt` 或 `config.json` 的 `token` 字段。16 个工具详见 `docs/procurement-system-spec.md` §5。

## 数据与备份

- 数据库：`~/procurement-data/procurement.db`（单文件，**升级永不触碰**）
- 备份：双击 `backup.bat` / `backup.sh`，或直接复制整个数据目录
- 换机：拷贝 `procurement-data` 到新机器 → 重跑 `install.sh`（数据随目录走）

## 开发

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest server/tests     # 61 个测试
./start.sh
```

CI（GitHub Actions）：push 跑三平台测试；打 tag 自动编译 Windows/macOS 单文件可执行 + skill 包并发 Release。

MIT License. 仅供采购成本核算，不含对外报价功能。
