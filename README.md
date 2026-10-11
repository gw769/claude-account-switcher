# Claude 帳號切換（Linux）

在 Linux 上讓 **Claude Desktop** 保存多個已登入的帳號，切換時不用重新登入。

此版本 `0.4.3-local` 支持 GUI 常驻，在账号选择界面查看每个账号的周额度、周重置时间、5 小时额度和 5 小时重置时间。切换时同步本机 **Code 分页**对话：A 的原始历史保留，B 从 Desktop 侧栏打开同一会话继续；切回 A 时带回 B 保存的新会话状态。普通 Chat 和云端会话仍绑定原账号。

**不能保证不封号。** 这是非官方工具；[Anthropic 消费者条款](https://www.anthropic.com/legal/consumer-terms)禁止绕过系统或保护措施。此版本提供手动切号、本机同步和只读用量查询，不自动轮换，不发送模型请求，不刷新或改写凭据。

适用于 Linux Claude Desktop（`claude-desktop` 套件）。本轮 `0.4.3-local` 在 101 的 Desktop `2.31226.0` 上实测：连续启动 3 次只保留 1 个主窗口；两个账号额度刷新成功，刷新前、中、后的窗口编号和窗口进程均相同，观察到 1 个独立查询子进程。蓝色图标的系统注册和窗口关联正确，刷新按钮位于右上角。安装和额度刷新验收期间，28 个登录和会话文件没有变化，Desktop 主进程没有重启；后续仍在使用中的会话可以继续保存。同步功能已做合成资料与假 app 测试，真实账号切换续聊尚未实测。

## 運作方式

```
~/.config/Claude               ← 使用中帳號的完整資料（真正的資料夾）

~/.config/Claude-accounts/
├── main  →  ~/.config/Claude  ← 使用中的帳號：指回去的連結
├── work/                      ← 其他帳號的完整資料（登入狀態、Claude Code 工作階段、設定…）
└── .switcher.log
```

Claude Desktop 永遠讀寫 `~/.config/Claude`，切換帳號就是：

1. 正常關閉 Claude Desktop
2. 重新读取最后保存的 Code 会话，把缺少或较新的索引和所需工作目录同步到目标账号；覆盖前备份
3. 把 `~/.config/Claude` 搬回 `Claude-accounts/<目前帳號>`，再把另一個帳號的資料夾搬到 `~/.config/Claude`（同一個磁碟內改名）
4. 重新開啟 Claude Desktop

因為 app 看到的路徑始終不變，**從應用程式選單、Dock 或 `claude://` 登入連結開啟的 Claude，都會自動使用目前的帳號**，不需要額外參數。

`~/.config/Claude` 是真正的資料夾而不是符號連結，所以 Claude Desktop 和 Claude Code 看到的路徑一致，Code 分頁的對話紀錄和記憶不會分成兩份。

## 安裝

需要：`claude-desktop`、`python3`、`zenity`、`python3-gi`、`gir1.2-gtk-3.0`（GNOME 通常已內建）。

实时额度读取还需要 `python3-cryptography`、`python3-gi`、`gir1.2-secret-1` 和已解锁的系统密钥环。缺少支持时仍可切号，额度栏会显示本机快照或不可用。

```bash
git clone https://github.com/gw769/claude-account-switcher.git
cd claude-account-switcher
bash install.sh
```

安裝到 `~/.local/bin/claude-account`，並在應用程式選單加入「Claude 帳號切換」。蓝色切换图标用于应用菜单、Dock、任务栏和窗口；右上角提供刷新额度按钮。同一桌面会话只允许一个账号主窗口，重复启动会唤起已有窗口，查询或切号期间也不会新开窗口。

日常使用只需打开应用菜单里的「Claude 账号切换」。

## 使用

### 第一次：加入第二個帳號

1. 開啟「Claude 帳號切換」→ **新增帳號…**
2. 替**目前登入**的帳號取名，例如 `main`
3. 替新帳號取名，例如 `work`
4. Claude Desktop 會關閉並以空白狀態重新開啟，登入第二個帳號即可

### 之後切換

開啟「Claude 帳號切換」→ **切換到「work」**。Claude Desktop 會關閉並以該帳號重新開啟。

### 在账号选择界面看额度

每个账号一行，直接显示 **周额度 / 周重置时间 / 5 小时额度 / 5 小时重置时间**。额度为套餐剩余百分比；重置时间按本机时区显示，旁边标明更新时间。

账号选择窗持续显示。刷新额度时启动独立子进程，查询期间保留旧表格并显示刷新中，完成后原地更新，不关闭或重建窗口。切号、同步、新增、移除、查看备份、取消操作或报错，全程都保留同一主窗口，只更新内容；选中当前账号不会退出。点 **关闭**、按 Esc 或关闭窗口才退出。额度查询由 **刷新额度** 按钮触发，窗口常驻不会自动查询或自动切号。

点击 **刷新额度** 后，读取各账号已有的 Desktop 登录状态，先核对服务器返回的账号身份，再只读查询用量。每个账号最多一分钟刷新一次；收到限流后按服务器要求等待。登录内容只在内存中使用，不打印、不缓存、不刷新，也不读取 CLI 的登录文件。

首次未刷新时可显示 Desktop 的本机百分比快照；快照没有重置时间，显示未知。服务器确认未启动的窗口显示未开始；记录已到重置时间后显示待刷新，不假定额度已经清零，也不推算下一次重置。登录过期、网络失败和限流都保留上次有效数据并标明状态。

用量查询使用 Desktop 同样访问的 `api.anthropic.com/api/oauth/profile` 和 `api.anthropic.com/api/oauth/usage`，这些接口没有公开的稳定性保证。官方用量页仍为准：[用量说明](https://support.claude.com/en/articles/9797557-usage-limit-best-practices)。界面布局和只读查询不代表封号风险已验证。

相同的 Desktop 登录状态读取和只读用量查询已有公开实现：[SnlperStripes 的 usage.go](https://github.com/SnlperStripes/claude-account-switcher/blob/main/usage.go)（Apache-2.0）和 [claude-usage-assistant](https://github.com/huanchong-99/claude-usage-assistant/blob/main/quota_card.py)（MIT）。本轮对 Linux 密钥环和多账号选择界面做了适配；开源前例不代表官方授权或零封号风险。

### 在 Desktop 接续原对话

1. 第二个账号第一次登录后，先打开 **Code** 分页，让 app 建立该账号的会话目录。
2. 打开「Claude 账号切换」→ **同步 Code 对话（目前账号）**。
3. Desktop 重新打开后，在 Code 侧栏点选原对话继续。此后每次手动切号都会同步，无需在终端恢复会话。

缺少完整历史或工作目录的会话会跳过并报数；身份不明、多组织、损坏 JSON、工作树登记冲突会停止同步或切号。功能限于本机 Code 会话。

### 备份与恢复

同步前，将目标旧索引和临时工作目录备份到该账号目录的 `.code-sync-backups/<批次>/before/`。同步失败会尝试恢复本次改动；恢复失败会明确报错并停止切号。图形界面的 **查看对话同步备份文件夹** 只在文件管理器打开这些自动保存的备份，不是同步功能开关。Code 对话同步默认已开启，每次手动切号都会执行。

手动恢复：关闭 Desktop，用文件管理器把该批次 `before/` 内的项放回账号目录的相同相对路径；`manifest.json` 里 `existed: false` 标记本次新增项，恢复时将这些项移走。原始 `~/.claude/projects/` 历史从不修改。安装程序只安装工具与菜单项，不会主动切号。

### 指令列

```
claude-account list                列出帳號，* 為使用中
claude-account switch <名稱>       切換帳號
claude-account add <名稱>          新增空白帳號並切換過去
claude-account init <名稱>         替目前登入的帳號命名（第一次使用）
claude-account remove <名稱>       把帳號的本機資料移到垃圾桶
claude-account restore             還原成普通的單一帳號設定
```

加上 `--yes` 可略過確認。

## 注意事項

- **切換會關閉 Claude Desktop**，正在執行的 Claude Code 工作階段會中斷。切換前會先詢問。
- 一次只能使用一個帳號（Claude Desktop 本身同時只能跑一個）。
- 移除帳號只會把**本機**登入資料移到垃圾桶，不會刪除你的 Claude 帳號。
- 若 Claude Desktop 在 30 秒內沒有關閉（例如正在等你確認某個對話框），會詢問是否強制關閉，不會自動強制關閉。
- 從 Claude Desktop 內的終端機執行時，切換會在背景繼續完成，結果以對話框顯示。
- 紀錄檔：`~/.config/Claude-accounts/.switcher.log`

## 哪些東西跟著帳號走，哪些共用

**每個帳號各自一份**（存在各自的資料夾，新帳號要重新設定）：

- Desktop app 設定：Bypass permissions 的同意（Settings → Claude Code）、Trusted Cowork folders、通知、預設瀏覽器、介面版面等。
- MCP 設定檔 `claude_desktop_config.json`。若新帳號也要相同的 MCP 伺服器，請自行複製該檔案。
- Code 分頁的對話列表：app 依帳號 ID 分開存放；此版本在切号或按「同步 Code 对话」时带入其他账号的完整本机会话。
- Chat 分頁的聊天記錄、claude.ai 上的 connectors 和外掛：存在 Anthropic 伺服器，本來就綁帳號。

**所有帳號共用**（在 `~/.claude` 或家目錄，不受切換影響）：

- `~/.claude/settings.json`（例如 `permissions.defaultMode`）、`~/.claude/CLAUDE.md`、`~/.claude/skills/`
- Claude Code 的記憶和對話紀錄檔（`~/.claude/projects/`）
- 終端機版 Claude Code，以及第三方模式的 `~/.config/Claude-3p`

**換帳號接著做同一件事：**

- 在 Desktop 的 Code 侧栏打开同步后的**同一会话**继续。下次切号时带回保存的新索引；完整历史留在原处。
- 「No folder」会话的临时工作目录按原固定路径同步，不把 `~/.config/Claude` 或 `scratch-workspaces` 改成共享符号链接。目标旧副本先备份；Git 工作树只带入已有匹配登记，不创建、切换或删除 Git 工作树。
- 另一账号的 MCP 或远端控制授权不会复制；需要时由 Desktop 重新确认。

## 從 0.1.x 升級

0.1.x 把 `~/.config/Claude` 做成指向 `Claude-accounts/<名稱>` 的符號連結。Claude Code 會解析連結、用真實路徑存對話紀錄，Claude Desktop 卻用連結路徑去讀，結果「No folder」對話的紀錄分成兩份：**app 顯示停在第一次切換前的舊內容，新的記憶也讀不到**。

升級方法：`bash install.sh` 之後，切換一次帳號即可（切到目前的帳號也可以：`claude-account switch <目前帳號>`，或在圖形介面選「轉換成新的資料夾配置」）。轉換時會：

- 把使用中的帳號搬到 `~/.config/Claude`，留下指回去的連結，舊路徑照樣能用
- 把只存在真實路徑下的對話紀錄資料夾（`~/.claude/projects/` 裡）搬到 app 讀的位置，舊位置留連結
- 新舊兩個位置**都有**紀錄的對話不會自動合併，只寫進紀錄檔並提示。這種情況通常是真實路徑那份比較新，確認後可手動把它放到 app 讀的位置

## 解除安裝

```bash
claude-account restore         # 讓使用中的帳號留在 ~/.config/Claude，變回單一帳號
bash install.sh --uninstall
```

其他帳號的資料會留在 `~/.config/Claude-accounts/`，確定不需要再自行刪除。

## 開發

```bash
bash tests/run.sh
python3 tests/test_sessions.py
python3 tests/test_usage.py
/usr/bin/python3 tests/test_gui.py  # 需要 GTK 3 和图形会话
```

Shell 测试使用兼容的 Zenity 界面验证假 app 的切号流程；`test_gui.py` 用真实 GTK 窗口、测试子进程和合成账号检查各动作全程保留主窗口、原地刷新、失败保留数据、关闭查询，以及刷新期间重复启动只唤起原窗口。本轮 117 项 Shell、18 项会话同步、20 项额度和 13 项 GTK 测试通过；全部测试不访问真实账号的额度接口。

測試使用假的 Claude 程序，暫存檔放在 repo 內的 `.test-tmp/`（不放 `/tmp`，跑完自動清掉），不會動到真正的 `~/.config/Claude` 或正在執行的 Claude。

测试通过只证明本机文件同步和假 app 切换流程，真实跨账号回复与封号风险未验证。存储结构参考 [xuelongmu/claude-desktop-switcher](https://github.com/xuelongmu/claude-desktop-switcher) 和 [erorplex/claude-desktop-profiles-macos](https://github.com/erorplex/claude-desktop-profiles-macos)。

## 致謝

構想來自 [Adam-Zaghloul/claude-desktop-switcher](https://github.com/Adam-Zaghloul/claude-desktop-switcher)。本專案改以搬動資料夾切換，並只關閉 Claude Desktop 主程序，避免 `pkill -f` 誤殺其他程式。

## 授權

MIT
