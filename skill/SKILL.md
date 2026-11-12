---
name: bom-procurement
description: 器件采购库的安装、维护与两项工作流——把公司内部各种格式的采购合同/报价清单解析入本地采购库；把客户 BOM 与库内物料比对并输出成本清单。当用户提到采购库、物资库、报价单入库、合同录入、BOM 比价、成本清单、查历史采购价时使用。
---

# 器件采购库（bom-procurement）

小企业本地采购库。**双形态**：人可双击独立使用（Web 界面），Agent 经 MCP 读写同一个 SQLite——两边数据实时一致。

## 三条红线（任何操作都不得违反）

1. **价格只能来自库内记录**：库里没有就留空、进未匹配清单，**绝不允许估算、推测或编造价格**
2. **可追溯**：每条价格都要带 `source`（`文件名#sheet#行号`）与 `price_date`
3. **不覆盖**：同型号多行 = 多条价格并存；人工确认过的对应关系必须写回别名表

## 先决条件：采购系统是否在运行

本 Skill 的所有能力都通过 MCP 调用本机采购系统（`http://127.0.0.1:8000/mcp`）。
调用失败/连接被拒时，**明确告诉用户「请先启动采购系统」**（双击 `start.bat` / `start.command`），不要改用其它数据源或凭记忆作答。

## 一、首次安装（方案 C：Skill 即安装器）

用户在已装 OneAgent 的机器上说「安装采购系统」时，按顺序执行：

1. **环境检查**：`python3 scripts/install.py --check`（Windows: `python scripts\install.py --check`）
   - 缺 Python 3.10+ → 给下载指引（Windows: python.org，勾选 Add to PATH；macOS: `brew install python@3.12`）
2. **执行安装**：`bash install.sh`（Windows: `install.bat`）
   安装器自动完成：布署代码 → 建 venv 装依赖 → 建库并生成随机接口口令 → 启动自检 → 注册开机自启 → 打印 MCP 配置参数
3. **配置 MCP**：用 OneAgent 的 MCP 管理能力添加服务器
   - URL：`http://127.0.0.1:8000/mcp`
   - Header：`Authorization: Bearer <安装器打印的口令>`
   - 失败时按 `references/mcp-config.md` 手动配置
4. **自检**：调用 `stats` 工具确认连通；回报安装结果与口令存放位置

目录约定（升级不碰数据）：
- 代码：`~/procurement-system`（Windows `%USERPROFILE%\procurement-system`）
- 数据：`~/procurement-data`（SQLite + config.json + 备份）
- 环境变量：`PROC_DATA_DIR` / `PROC_CODE_DIR` / `PROC_PORT`

## 二、工作流 1：资料录入（采购合同 / 报价清单 → 入库）

用户给了文件时：

1. **解析预览**：调 `parse_import_file(path=..., price_type="")`
   - **支持格式**：xlsx / xls / csv / docx / **pdf** / **图片（png/jpg/jpeg/bmp/tif/tiff/webp）**
   - PDF 自动分流：有文本层 → 直接抽表格；**扫描件 → 自动渲染成图走 OCR**
   - 图片 → OCR（macOS 用系统 Vision；Windows/Linux 用 RapidOCR 或 tesseract）
   - 自动按文件名判定价格类型（采购/进货→purchase；合同/协议→contract；报价/询价→quote）
   - 拿到 `rows`（可入库）、`pending`（需人工）、`summary`（含 `ocr.backend`/`confidence`）
2. **给用户看预览**：列出前若干行（型号/单价/数量/供应商/来源），
   报告「可入库 N 行 / 需人工 M 行 / 库内已有同型号 K 行」。**必须等用户确认**（FR-17）
   - ⚠️ 若返回带 `review_hint` 或 `ocr` 字段，说明是 OCR 识别结果，**必须提醒用户核对型号与单价**
     （OCR 易把 `I3` 认成 `13`、把序号与型号粘在一起），并说明可在界面里直接改
3. **确认后入库**：调 `import_parsed(rows=..., pending=...)`
   - 价格无法解析的行只建物料不建价（不会伪造价格）
   - `pending` 写入待确认区待人工处理
4. **报告结果**：`summary` 的 ok / item_only / fail / pending_added

注意：
- 图片/扫描件识别质量取决于清晰度；列粘连、序号粘连已在解析层自动处理，字符级误读需人工核对
- 区间价（12~15）会保守取上限并附提示；表头疑似「总价」时会提示人工核对
- 绝不跳过预览直接入库

## 三、工作流 2：BOM 比对（客户 BOM → 成本清单）

用户给了客户 BOM（Excel）时：

1. **读 BOM**：解析出每行的 型号/数量/单位，保留原始行号
2. **逐行查库**：调 `search_items(query=型号, ...)`
   - `exact` / `alias` → 可直接用，返回里已带 `latest_price`
   - `fuzzy` → **只能作为候选给用户挑**，不得直接取价
   - `none` → 进未匹配清单
3. **取价**：`purchase` 优先 > `contract` > `quote`（同类型取日期最新）；仅 `quote` 时标注「参考价」
4. **单位不一致**：按 `unit_conversions` 换算；无记录 → 标「单位待确认」，**不硬算**
5. **输出成本清单**（Excel，用本机脚本生成）：
   - 主表：`BOM行号 | 客户名称 | 客户型号 | 数量 | 单位 | 匹配状态 | 内部型号 | 品牌 | 品类 | 规格 | 价格类型 | 单价 | 计价单位 | 换算 | 小计 | 价格日期 | 供应商 | 税口径 | 来源 | 备注`
   - 侧表①：未匹配清单（含前 3 候选与建议）
   - 侧表②：口径异常（单位待确认/区间价/税口径未知/价格超 12 个月）
   - 底部：分类小计 + 合计 + **有价行覆盖率**（低于 80% 要提示不能作为决策依据）
6. **人工确认回流**：用户确认某个模糊候选后，调 `add_alias(alias_text=原始写法, item_id=...)`，下次直接命中

## 四、日常维护动作

| 用户说 | 调用 |
|---|---|
| 查某个型号/物料 | `search_items` → `get_item` / `get_prices` |
| 手工加物料 | `upsert_item` |
| 手工加价格 | `upsert_price`（带 source！） |
| 维护待确认区 | `list_pending` → `confirm_pending` / 让用户在界面忽略 |
| 单位换算 | `upsert_conversion`（如 箱→米 factor=305） |
| 库情况 | `stats` |
| 备份 | 双击 `backup.bat` / `backup.sh`，或提示用户复制数据目录 |

## 五、故障处理

| 现象 | 处理 |
|---|---|
| MCP 调用连接被拒 | 系统没启动 → 让用户双击 `start.bat`/`start.command`；装过但没自启 → 重新 `install.sh` |
| 401 口令错误 | 口令在数据目录 `接口口令.txt` / `config.json`；让用户重新粘贴到 OneAgent MCP 配置 |
| 端口被占 | 用 `PROC_PORT=8001` 重启，并同步改 MCP 配置里的 URL |
| 解析返回「不支持格式」 | 支持 xlsx/xls/csv/docx/pdf/png/jpg/jpeg/bmp/tif/tiff/webp |
| 图片/PDF 报「没有可用的 OCR 后端」 | Windows/Linux 执行 `pip install rapidocr-onnxruntime`；macOS 需系统 swift |
| OCR 结果型号有误（如 I3→13） | 正常现象：在预览表里直接改后再入库（这是预览存在的意义） |
| 解析出 0 行 | 表头没识别到 → 让用户确认首行是否为「型号/单价」类表头，或人工用界面录入 |

## 六、口径约定

- `price_type`：`purchase` 采购成本 / `quote` 报价参考 / `contract` 合同价
- `tax_mode`：`included` 含税 / `excluded` 未税 / `unknown` 未标注（**不做擅自折算**，原样带标签）
- 单位：`items.unit` 是基本单位；`prices.unit` 是计价单位，两者可以不同，靠 `unit_conversions` 桥接
- 只出采购成本，**不做对外报价**（不加价、不算折扣）
