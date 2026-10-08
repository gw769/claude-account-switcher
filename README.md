# Claude 帳號切換（Linux）

在 Linux 上讓 **Claude Desktop** 保存多個已登入的帳號，切換時不用重新登入。

適用於 Anthropic 官方的 Linux 版 Claude Desktop（`claude-desktop` 套件），在 Ubuntu 26.04 + GNOME 上開發與測試。

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
2. 把 `~/.config/Claude` 搬回 `Claude-accounts/<目前帳號>`，再把另一個帳號的資料夾搬到 `~/.config/Claude`（同一個磁碟內改名，瞬間完成）
3. 重新開啟 Claude Desktop

因為 app 看到的路徑始終不變，**從應用程式選單、Dock 或 `claude://` 登入連結開啟的 Claude，都會自動使用目前的帳號**，不需要額外參數。

`~/.config/Claude` 是真正的資料夾而不是符號連結，所以 Claude Desktop 和 Claude Code 看到的路徑一致，Code 分頁的對話紀錄和記憶不會分成兩份。

## 安裝

需要：官方 `claude-desktop`、`zenity`（GNOME 通常已內建）。

```bash
git clone https://github.com/gw769/claude-account-switcher.git
cd claude-account-switcher
bash install.sh
```

安裝到 `~/.local/bin/claude-account`，並在應用程式選單加入「Claude 帳號切換」。

## 使用

### 第一次：加入第二個帳號

1. 開啟「Claude 帳號切換」→ **新增帳號…**
2. 替**目前登入**的帳號取名，例如 `main`
3. 替新帳號取名，例如 `work`
4. Claude Desktop 會關閉並以空白狀態重新開啟，登入第二個帳號即可

### 之後切換

開啟「Claude 帳號切換」→ **切換到「work」**。Claude Desktop 會關閉並以該帳號重新開啟。

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
- Code 分頁的對話列表：app 依帳號 ID 分開存放，另一個帳號看不到。
- Chat 分頁的聊天記錄、claude.ai 上的 connectors 和外掛：存在 Anthropic 伺服器，本來就綁帳號。

**所有帳號共用**（在 `~/.claude` 或家目錄，不受切換影響）：

- `~/.claude/settings.json`（例如 `permissions.defaultMode`）、`~/.claude/CLAUDE.md`、`~/.claude/skills/`
- Claude Code 的記憶和對話紀錄檔（`~/.claude/projects/`）
- 終端機版 Claude Code，以及第三方模式的 `~/.config/Claude-3p`

**換帳號接著做同一件事：**

- 在兩個帳號都打開**同一個專案資料夾**開 Code 對話。檔案、git 和記憶都在，只是對話列表不同。
- 要接續完整對話，可以在終端機執行 `claude --resume <對話 ID> --fork-session`。

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
```

測試使用假的 Claude 程序，暫存檔放在 repo 內的 `.test-tmp/`（不放 `/tmp`，跑完自動清掉），不會動到真正的 `~/.config/Claude` 或正在執行的 Claude。

## 致謝

構想來自 [Adam-Zaghloul/claude-desktop-switcher](https://github.com/Adam-Zaghloul/claude-desktop-switcher)。本專案改以搬動資料夾切換，並只關閉 Claude Desktop 主程序，避免 `pkill -f` 誤殺其他程式。

## 授權

MIT
