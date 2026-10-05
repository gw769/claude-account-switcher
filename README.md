# Claude 帳號切換（Linux）

在 Linux 上讓 **Claude Desktop** 保存多個已登入的帳號，切換時不用重新登入。

適用於 Anthropic 官方的 Linux 版 Claude Desktop（`claude-desktop` 套件），在 Ubuntu 26.04 + GNOME 上開發與測試。

## 運作方式

```
~/.config/Claude  →  ~/.config/Claude-accounts/<使用中的帳號>   （符號連結）

~/.config/Claude-accounts/
├── main/      ← 帳號 1 的完整資料（登入狀態、Claude Code 工作階段、設定…）
├── work/      ← 帳號 2
└── .switcher.log
```

Claude Desktop 永遠讀寫 `~/.config/Claude`，切換帳號就是：

1. 正常關閉 Claude Desktop
2. 把 `~/.config/Claude` 這個連結改指向另一個帳號的資料夾
3. 重新開啟 Claude Desktop

因為 app 看到的路徑始終不變，**從應用程式選單、Dock 或 `claude://` 登入連結開啟的 Claude，都會自動使用目前的帳號**，不需要額外參數。

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
- 每個帳號有自己的設定，包括 MCP 設定檔 `claude_desktop_config.json`。若新帳號也要相同的 MCP 伺服器，請自行複製該檔案。
- 終端機版 Claude Code（`~/.claude`）和第三方模式的 `~/.config/Claude-3p` 不受影響，所有帳號共用。
- 移除帳號只會把**本機**登入資料移到垃圾桶，不會刪除你的 Claude 帳號。
- 若 Claude Desktop 在 30 秒內沒有關閉（例如正在等你確認某個對話框），會詢問是否強制關閉，不會自動強制關閉。
- 從 Claude Desktop 內的終端機執行時，切換會在背景繼續完成，結果以對話框顯示。
- 紀錄檔：`~/.config/Claude-accounts/.switcher.log`

## 解除安裝

```bash
claude-account restore         # 把使用中的帳號放回 ~/.config/Claude
bash install.sh --uninstall
```

其他帳號的資料會留在 `~/.config/Claude-accounts/`，確定不需要再自行刪除。

## 開發

```bash
bash tests/run.sh
```

測試使用假的 Claude 程序與暫存資料夾，不會動到真正的 `~/.config/Claude` 或正在執行的 Claude。

## 致謝

構想來自 [Adam-Zaghloul/claude-desktop-switcher](https://github.com/Adam-Zaghloul/claude-desktop-switcher)。本專案改以符號連結切換，並只關閉 Claude Desktop 主程序，避免 `pkill -f` 誤殺其他程式。

## 授權

MIT
