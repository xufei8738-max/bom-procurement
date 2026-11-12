# macOS 安装说明

本项目 macOS 版本**未做 Apple 开发者签名与公证**（自用场景，不购买开发者账号），
因此 macOS 的 Gatekeeper 会拦截首次运行。本文给出三种可行的安装方式，**按推荐顺序排列**。

> 遇到「无法打开」「已损坏，无法打开」「来自身份不明的开发者」「已被阻止」等提示，都是同一个原因，
> 按下面任一方式处理即可，**文件本身没有损坏**。

---

## 方式一：源码安装（最推荐，不触发 Gatekeeper）

完全在本机创建虚拟环境、安装依赖，不下载可执行文件，**不会触发任何安全拦截**。

```bash
git clone https://github.com/xufei8738-max/bom-procurement.git
cd bom-procurement
bash install.sh
```

`install.sh` 会自动完成：环境检查 → 创建 venv → 安装依赖（清华镜像失败自动回退官方源）
→ 建库并生成接口口令 → 启动自检 → 注册登录自启（launchd）→ 打印 MCP 配置参数。

**依赖**：macOS 10.15+ 与 Python 3.10+。缺 Python 时：

```bash
brew install python@3.12        # 或从 python.org 下载安装
```

**日常启动**：双击 `start.command`（Finder 中双击即可），或：

```bash
~/procurement-system/start.sh
```

**卸载**：`python3 scripts/install.py --uninstall`（代码目录删除，**数据目录保留**）。

---

## 方式二：下载可执行文件 + 命令行放行（最可靠的一条命令）

从 [Releases](https://github.com/xufei8738-max/bom-procurement/releases) 下载 `bom-procurement`（无后缀）。

打开「终端」（应用程序 → 实用工具 → 终端），执行：

```bash
cd ~/Downloads
chmod +x bom-procurement
xattr -d com.apple.quarantine bom-procurement
./bom-procurement
```

三行命令的作用：
| 命令 | 作用 |
|---|---|
| `chmod +x` | 赋予可执行权限（下载的文件默认没有） |
| `xattr -d com.apple.quarantine` | **移除"从网上下载"标记**，Gatekeeper 不再拦截 |
| `./bom-procurement` | 启动 |

启动后会自动打开浏览器（`http://127.0.0.1:8000`），接口口令在终端窗口打印。

> 若提示 `xattr: No such file`：说明该文件本来就没有隔离标记，直接执行第三行即可。

---

## 方式三：系统设置放行（图形界面）

1. 双击 `bom-procurement`，出现拦截提示 → 点「完成」或「好」（不要点「移到废纸篓」）
2. 打开 **系统设置 → 隐私与安全性**
3. 向下滚动到「安全性」区域，会看到一条 **「已阻止使用"bom-procurement"，因为它来自身份不明的开发者」**
4. 点旁边的 **「仍要打开」**，输入开机密码确认
5. 再次双击运行，弹窗中点「打开」

> ⚠️ 注意：macOS 15 及更高版本（包括 macOS 26/27）**不再稳定提供这个入口**。
> 若在「隐私与安全性」里找不到该提示，请改用方式一或方式二。

---

## 方式四：右键打开（成功率最低）

Finder 中**按住 Control 键点击**文件（或右键）→ 选择「打开」→ 弹窗中再点「打开」。

> macOS 15+ 已大幅削弱此方式，仅作补充。

---

## 常见问题

### 「bom-procurement 已损坏，无法打开。你应该将它移到废纸篓。」

这是 **ad-hoc 签名与隔离属性共同作用**的提示，**并非文件损坏**。用方式二的命令处理：

```bash
xattr -d com.apple.quarantine bom-procurement
```

若该命令无效，强制清除全部扩展属性：

```bash
xattr -c bom-procurement
```

### 「无法验证开发者」

方式三（系统设置 → 隐私与安全性 → 仍要打开）或方式二。

### 双击没反应 / 闪一下就没了

多半是程序启动时报错退出。用终端运行看错误信息：

```bash
./bom-procurement
```

常见原因：端口 8000 被占用（换端口：`PROC_PORT=8001 ./bom-procurement`）。

### 提示「Apple 无法检查其是否包含恶意软件」

同方式三处理。

### 如何确认程序是安全的？

本项目 MIT 开源，全部代码可审：

- 主程序逻辑：`launcher.py` + `server/` 目录
- 网络行为：**只监听 `127.0.0.1:8000`**，不连接任何外部服务器
- 数据存储：全部在本机 `~/procurement-data/`（SQLite 单文件），不上传任何数据

如需自行编译验证，见仓库 `.github/workflows/release.yml` 中使用的 PyInstaller 命令。

---

## 为什么不签名？

Apple 开发者账号 99 美元/年。本项目为自用工具，未购买账号，因此：

- 无法做正式代码签名与公证（notarization）
- 首次运行必须由用户手动放行一次（**仅第一次**，之后正常双击即可）

若未来需要分发给他人使用，建议购买账号并配置签名 + 公证流程。

---

## 数据位置与备份

| 项 | 路径 |
|---|---|
| 数据目录 | `~/procurement-data/` |
| 数据库 | `~/procurement-data/procurement.db` |
| 接口口令 | `~/procurement-data/接口口令.txt` |

**备份**：`bash backup.sh` 或直接复制整个 `~/procurement-data` 目录。
**换机**：把 `procurement-data` 拷到新机器，重新跑一次 `install.sh` 即可（数据随目录走）。
