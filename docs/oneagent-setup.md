# OneAgent 对接与 Skill 安装（详细步骤）

本文解决两个最常卡住的问题：
1. 采购系统装好后，**OneAgent 怎么连上它**（MCP 配置详细步骤）
2. **Skill 包怎么装**（`.skill` / `.zip` 两种文件、三种安装方式）

---

## 一、前置：先把采购系统跑起来

无论哪种对接方式，都需要采购系统**正在运行**。判断方法：浏览器打开
`http://127.0.0.1:8000`，能看到「器件采购库」界面即运行中。

没在运行的话：

| 安装方式 | 启动方法 |
|---|---|
| exe（Windows） | 双击 `bom-procurement.exe` |
| exe（macOS） | 终端 `./bom-procurement`（首次需放行，见 [install-macos.md](install-macos.md)） |
| 源码安装 | 双击 `start.command`（macOS）/ 双击 `start.bat`（Windows） |

**获取接口口令**（对接必需，二选一）：
- 看启动时的终端输出，有一行「接口口令：xxxx」
- 打开数据目录 `~/procurement-data/接口口令.txt`（Windows：`%USERPROFILE%\procurement-data\接口口令.txt`）

---

## 二、OneAgent 添加 MCP 服务器（详细步骤）

### 步骤 1：打开 MCP 管理页

在 OneAgent 里打开 **设置 → 系统工具 / MCP 服务器管理**（不同版本入口名称略有差异，
找不到时直接在对话里对 OneAgent 说「打开 MCP 管理页面」）。

### 步骤 2：添加服务器，按下表填写

| 配置项 | 填什么 |
|---|---|
| 名称 | `bom-procurement`（随便起，认得出来即可） |
| 传输方式 / 类型 | **http**（如选项是 "http/sse" 或 "streamable HTTP"，选它） |
| URL | `http://127.0.0.1:8000/mcp`（**注意结尾有 /mcp**，别漏） |
| 请求头 Header | 键名 `Authorization`，值 `Bearer 你的口令`（**Bearer 后有一个空格**，然后粘贴口令，不要再加引号） |

填好后示例（口令以实际为准）：

```
URL:     http://127.0.0.1:8000/mcp
Header:  Authorization: Bearer mncPuVB7bApglM2JS5FVnw
```

### 步骤 3：测试连接

点「测试连接」/「保存」。成功标志：工具列表里出现 `search_items`、`upsert_price`、
`compare_bom` 等（共 19 个工具）。

### 步骤 4：验证

对 OneAgent 说：

> 查一下采购库的统计

它应该调用 `stats` 工具，返回物料数、价格记录数等。到这一步对接完成。

### 常见失败排查

| 现象 | 原因与处理 |
|---|---|
| 连接被拒 / Connection refused | 采购系统没启动，先把它跑起来 |
| 401 / 未授权 | 口令粘贴错误。注意 `Bearer` 后**有一个空格**；口令去 `接口口令.txt` 重新复制 |
| 404 | URL 少了结尾的 `/mcp`，补上 |
| 加载超时 | 检查是否有别的程序占了 8000 端口；或用 `PROC_PORT=8001` 启动系统并把 URL 改成 8001 |
| 工具列表为空 | 系统版本低于 v0.2.0，重新下载新版 |

---

## 三、Skill 安装（三种方式任选其一）

### 先说清两个文件是什么

Release 页会看到两个内容完全相同的包：

| 文件 | 用途 |
|---|---|
| `bom-procurement.zip` | **给电脑用的普通 zip**，双击即可解压（推荐手动安装的人下这个） |
| `bom-procurement.skill` | 给 OneAgent 技能系统识别的格式（**本质也是 zip**，只是扩展名不同） |

> 如果已经下了 `.skill` 解不开：它就是 zip，改名为 `bom-procurement.zip` 或
> 用命令解压（macOS/Linux：`unzip bom-procurement.skill`；Windows：用 7-Zip / WinRAR，
> 或先改后缀为 .zip 再右键解压）。**下个版本起 Release 会同时提供 .zip，无需再折腾**。

### 方式 A：只要系统能用——解压安装（最简单）

1. 下载 `bom-procurement.zip`，**双击解压**
2. 打开终端（macOS）或命令行（Windows），进入解压出的 `bom-procurement` 目录：
   ```bash
   # macOS / Linux
   cd ~/Downloads/bom-procurement
   bash install.sh
   ```
   ```bat
   :: Windows
   cd %USERPROFILE%\Downloads\bom-procurement
   install.bat
   ```
3. 安装器自动完成：环境检查 → 建 venv 装依赖 → 建库生成口令 → 自检 → 注册开机自启
   → **在最后打印 MCP 配置参数（URL 和口令）**，照着本文第二节填进 OneAgent 即可

> 方式 A 装完就拥有完整功能（界面 + MCP）。Skill 文件不是必需品——
> 它只是给 OneAgent 提供操作说明（SKILL.md），让 Agent 知道怎么用这些工具。

### 方式 B：让 OneAgent 认识这个 Skill（对话式安装）

前提：采购系统已通过方式 A 安装（或 exe 运行中），OneAgent 已连上 MCP。

1. 把解压后的目录整个交给 OneAgent：在对话里说「安装采购系统 skill，文件在 ~/Downloads/bom-procurement」
2. OneAgent 读取其中的 `SKILL.md`，按说明完成检查/注册
3. 之后就可以直接对话：「把这份报价单录进采购库」「这份 BOM 出个成本清单」

### 方式 C：通过 OneAgent 技能面板导入 `.skill`

如果 OneAgent 的设置里有「技能 / Skills」面板且支持导入：

1. 用 `bom-procurement.skill` 文件直接导入
2. 导入后对 OneAgent 说「安装采购系统」，它会执行 SKILL.md 里的安装流程

---

## 四、装好之后：试一遍完整流程

```
① 录入：对 OneAgent 说「把 ~/Documents/采购清单.xlsx 录入采购库」
   → Agent 解析、给你预览 → 你确认 → 入库

② 比对：对 OneAgent 说「用 ~/Documents/客户BOM.xlsx 出一份成本清单」
   → Agent 逐行匹配取价 → 报告命中率与覆盖率 → 生成 Excel

③ 人工兜底：Agent 报「2 行模糊候选」→ 你说「第 2 行用候选 1」
   → Agent 写别名回流 → 重新比对 → 覆盖率上升
```

---

## 五、升级与卸载

| 操作 | 命令 |
|---|---|
| 升级 | 下载新版 zip 解压覆盖，或 `git pull` 后重跑 `bash install.sh`（**数据目录不受影响**） |
| 卸载 | `python3 scripts/install.py --uninstall`（删代码与自启，**数据保留**） |
| 彻底删除 | 卸载后手动删除 `~/procurement-data` 目录 |
| 换机 | 复制 `~/procurement-data` 到新机器 → 新机器跑一遍安装 → 数据随目录走 |
