# pygit
A from-scratch Git implementation in pure Python that reads and writes real Git repositories.

pygit 是一個只用 Python 標準函式庫寫成的 Git。它不是「呼叫 git 指令的包裝」，而是自己讀寫 `.git` 裡的每一個位元組：物件（blob / tree / commit / tag）、index、refs、packfile，連 diff 演算法（Myers）都是自己實作。

最重要的一點是**跟真正的 git 互通**：

- 用 pygit 做出來的 repo，真的 `git log`、`git fsck --strict` 都讀得懂、沒有錯誤。
- 用真的 git 做出來的 repo（包含 merge commit、annotated tag，甚至 `git gc` 之後的 packfile），pygit 也讀得懂。

這個專案的目的是學習：想知道 `git add`、`git commit` 到底對硬碟做了什麼事，最快的方法就是自己寫一個。README 的後半段有一大章「Git 內部是怎麼運作的」，用 ASCII 圖從頭講一遍。

## 功能

底層指令（plumbing）：

| 指令 | 說明 |
| --- | --- |
| `init [dir] [-b branch]` | 建立空的 repo |
| `hash-object [-w] [-t type] [--stdin] [file...]` | 計算（並可寫入）物件的 SHA-1 |
| `cat-file (-t \| -s \| -p \| -e) <object>` | 看物件的型別、大小、內容 |
| `write-tree` | 把 index 寫成 tree 物件 |
| `commit-tree <tree> [-p parent]... [-m msg]` | 直接建立 commit 物件（可多個 parent） |
| `update-ref [-d] <ref> <sha>` | 建立、更新、刪除 ref |
| `symbolic-ref <name> [target]` | 讀寫 HEAD 這類符號參照 |
| `rev-parse <rev>...` | 解析 `HEAD`、分支名、tag、短 hash、`HEAD~2`、`HEAD^`、`v1^{tree}`、`HEAD:path` |
| `ls-files [-s] [-z]` | 列出 index 內容 |
| `ls-tree [-r] [--name-only] [-z] <tree-ish>` | 列出 tree 內容 |

高層指令（porcelain）：

| 指令 | 說明 |
| --- | --- |
| `add <path>...` | 加入 index（檔案、目錄、`.`），遵守 `.gitignore`；已刪除的檔案會一併標記刪除 |
| `rm [--cached] [-r] [-f] <path>...` | 從 index（和工作區）移除 |
| `commit -m <msg> [-a] [--allow-empty]` | 建立 commit；作者取自環境變數或 `.git/config` 的 `[user]` |
| `status [-s] [--porcelain] [-z]` | 已暫存 / 未暫存 / 未追蹤 |
| `log [--oneline] [--graph] [-n N] [rev...]` | 歷史紀錄 |
| `diff [--cached] [path...]` | unified diff（自己實作 Myers 演算法） |
| `branch [-d\|-D] [-v] [name [start]]` | 列出、建立、刪除分支 |
| `switch [-c] <branch>` / `checkout [-b] <branch\|commit>` | 切換分支並更新工作區與 index；有衝突的本地修改會拒絕 |
| `checkout -- <path>...` | 用 index 的內容還原檔案 |
| `tag [-a] [-m msg] [-d] [name [rev]]` | 輕量 tag 與 annotated tag |
| `show [object]` | 顯示 commit（含 diff）、tag、tree、blob |
| `config [--global] <key> [value]` | 讀寫設定 |

其他重點：

- index 完整讀寫 DIRC version 2（含 8 位元組對齊、SHA-1 checksum）；可讀 v3；遇到 v4 或必要的 extension 會清楚報錯，選用的 extension（如 `TREE`）會略過。
- 讀取 packfile：`.idx` v2、`.pack`、`OFS_DELTA` / `REF_DELTA`，也讀 `packed-refs`，所以 `git gc` 過的 repo 也能用（只讀，pygit 只寫 loose object）。
- `.gitignore`：萬用字元（`*` `?` `[]` `**`）、目錄結尾 `/`、`!` 否定、`#` 註解、開頭 `/` 錨定、巢狀 `.gitignore`、`.git/info/exclude`。
- 偵測修改用 index 的 stat 快取（大小、mtime、inode），對不上才重新計算內容 hash，還處理了 racy git 的情況。
- 正確處理中文檔名、空白檔名、可執行權限（100755）、symlink（120000）。
- 錯誤訊息一律用英文，格式跟 git 一樣（`fatal: ...` / `error: ...`）；不在 repo 內執行會顯示 `fatal: not a git repository`。

## 安裝與執行

### 需要什麼

- Python 3.10 或更新版本（在終端機輸入 `python3 --version` 檢查）
- 如果想跟真的 git 對照或跑測試，需要安裝 git（`git --version` 檢查）

pygit 本身**不需要安裝任何第三方套件**。

### 方法一：用 pipx 或 uv 安裝（推薦）

這會把 `pygit` 指令安裝到你的電腦上，任何資料夾都能用。

```bash
# 用 pipx（沒有的話先 pip install pipx，或 brew install pipx）
pipx install git+https://github.com/useless-husband/pygit

# 或者用 uv
uv tool install git+https://github.com/useless-husband/pygit
```

裝好後確認：

```bash
pygit --version
```

### 方法二：直接從原始碼執行（不安裝）

```bash
git clone https://github.com/useless-husband/pygit
cd pygit
PYTHONPATH=src python3 -m pygit --version
```

之後每個指令都用 `PYTHONPATH=src python3 -m pygit <指令>` 代替 `pygit <指令>`。想省事可以在同一個終端機視窗先執行一次 `export PYTHONPATH=$PWD/src`，然後用 `python3 -m pygit <指令>`。

### 方法三：本機開發安裝

```bash
git clone https://github.com/useless-husband/pygit
cd pygit
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pygit --version
```

### 第一次使用

```bash
mkdir my-project
cd my-project
pygit init
pygit config user.name "你的名字"
pygit config user.email "you@example.com"
echo "hello" > hello.txt
pygit add hello.txt
pygit commit -m "first commit"
pygit log
```

## 使用範例

以下是真實的終端機輸出（時間用環境變數固定）。

```console
$ pygit init
Initialized empty Git repository in /tmp/demo/hello/.git/

$ printf 'print("hello")\n' > hello.py
$ echo "*.log" > .gitignore
$ echo x > debug.log

$ pygit status
On branch main

No commits yet

Untracked files:
  (use "pygit add <file>..." to include in what will be committed)
	.gitignore
	hello.py

nothing added to commit but untracked files present (use "pygit add" to track)

$ pygit add .
$ pygit status -s
## main (no commits yet)
A  .gitignore
A  hello.py

$ pygit commit -m "first commit"
[main (root-commit) 470eed5] first commit
```

`debug.log` 符合 `.gitignore` 的 `*.log`，所以不會出現在 status，也不會被 `add .` 加進去。

修改檔案、看 diff、再 commit：

```console
$ printf 'print("hello")\nprint("world")\n' > hello.py

$ pygit diff
diff --git a/hello.py b/hello.py
index 11b15b1..3ef823c 100644
--- a/hello.py
+++ b/hello.py
@@ -1 +1,2 @@
 print("hello")
+print("world")

$ pygit add hello.py
$ pygit commit -m "say world"
[main dcdc23b] say world
```

分支與歷史：

```console
$ pygit branch feature
$ pygit switch feature
Switched to branch 'feature'

$ pygit branch
* feature
  main

$ pygit log --oneline
dcdc23b say world
470eed5 first commit
```

看底層物件：

```console
$ pygit cat-file -p HEAD
tree 1e42028450d3c33a8437d68272a27b4583622dcb
parent 470eed5fa84c97baed7cddd828d98a160f8931dc
author Ada Chen <ada@example.com> 1772416800 +0800
committer Ada Chen <ada@example.com> 1772416800 +0800

say world

$ pygit ls-files --stage
100644 397b4a7624e35fa60563a9c03b1213d93f7b6546 0	.gitignore
100644 3ef823c81a89f80572a15cfe8428f3b3e05f8a8a 0	hello.py

$ pygit rev-parse HEAD~1
470eed5fa84c97baed7cddd828d98a160f8931dc
```

**用真的 git 驗證 pygit 做出來的東西：**

```console
$ git fsck --strict && echo "git fsck: ok"
git fsck: ok

$ git log --oneline
dcdc23b say world
470eed5 first commit
```

兩個工具可以混著用：例如用 `git commit` 建立 merge commit，再用 `pygit log --graph` 看；或用 `pygit add` 之後用 `git commit`，反過來也可以。

## 專案結構

```
pygit/
├── pyproject.toml            打包設定；console script: pygit
├── src/pygit/
│   ├── cli.py                argparse 指令列入口
│   ├── commands.py           每個子指令的實作
│   ├── repo.py               Repo 類別：找 .git、HEAD、index、身分、寫 commit/tree
│   ├── objects.py            blob / tree / commit / tag 的格式與 SHA-1
│   ├── odb.py                物件資料庫：loose object 讀寫 + 從 pack 讀
│   ├── pack.py               packfile / idx v2 讀取、delta 還原
│   ├── index.py              .git/index (DIRC v2) 讀寫
│   ├── refs.py               refs、HEAD、packed-refs
│   ├── revparse.py           HEAD~2、main^、短 hash、v1^{tree}、HEAD:path
│   ├── ignore.py             .gitignore 比對
│   ├── worktree.py           stat 快取、暫存檔案、status 計算、checkout
│   ├── diff.py               Myers diff 與 unified diff 輸出
│   └── util.py               錯誤類別、日期、路徑引號、設定檔解析
├── tests/                    unittest；大量與真的 git 交叉驗證
└── .github/workflows/ci.yml  Ubuntu + macOS、Python 3.10 到 3.13
```

## 如何跑測試

只需要 Python 與 git，不必安裝任何東西：

```bash
git clone https://github.com/useless-husband/pygit
cd pygit
python3 -m unittest discover -s tests -v
```

只跑某一個檔案：

```bash
python3 -m unittest discover -s tests -p "test_diff.py" -v
```

測試的做法：

- 每個測試都在暫存資料夾裡建 repo，測完刪除。
- 作者、committer、時間戳記都用環境變數固定（`GIT_AUTHOR_DATE` 等），所以結果是決定性的，連跑多次都一樣。
- 大部分測試是「交叉驗證」：同一份輸入同時交給 git 和 pygit，比對輸出是否逐位元組相同。例如 `hash-object`、`ls-files --stage`、`status --porcelain -z`、`diff`、`log`、`show`、`rev-parse`、`cat-file`。
- 反方向也驗證：pygit 寫出的 commit 要通過 `git fsck --strict`。
- Packfile 的測試用 `git repack` 與 `git gc` 產生 OFS_DELTA 與 REF_DELTA，並確認 pygit 讀出的每個物件都跟 `git cat-file --batch` 一樣。
- Myers diff 有單元測試，還用亂數輸入驗證「結果最短」且「套用後能還原」。

---

# Git 內部是怎麼運作的

這一章不需要先懂 Git。讀完你會知道 `.git` 資料夾裡到底放了什麼，以及 pygit 每個模組在做什麼。你可以一邊讀，一邊在 repo 裡用 `pygit cat-file`、`pygit ls-files --stage` 或者 `find .git` 實際看。

## 1. 大圖：Git 是一個「內容定址」的資料庫

Git 的核心是一個很簡單的想法：**把每一份內容用它的雜湊值（SHA-1）當名字存起來**。

```
      你的檔案內容                   .git/objects/ 裡的檔案
  ┌────────────────────┐  SHA-1   ┌──────────────────────────────┐
  │ hello\n            │ ───────▶ │ ce/013625030ba8dba906f7569...│
  └────────────────────┘          └──────────────────────────────┘
```

同樣的內容一定得到同樣的名字，所以重複的檔案只會存一份；內容只要差一個位元，名字就完全不同，所以資料損壞時能立刻發現。

在這個資料庫上面，Git 只有四種物件，分別是：

```
   commit ──▶ tree ──▶ blob     （檔案內容）
      │         └───▶ tree ──▶ blob
      └──▶ parent commit
   tag ──▶ commit（給某個 commit 一個有簽名資訊的名字）
```

- **blob**：一個檔案的內容（不含檔名）。
- **tree**：一個資料夾。列出「檔名 → blob 或子 tree」。
- **commit**：一個快照。指向一個 tree（整個專案的根資料夾），加上父 commit、作者、時間、訊息。
- **tag**（annotated tag）：指向另一個物件，附上標籤名、建立者、訊息。

其他所有東西（分支、HEAD、標籤名）都只是「指向某個 commit 的名字」。

## 2. `.git` 目錄結構

`pygit init` 之後，再 commit 一次，你會看到：

```
.git/
├── HEAD                ← 內容："ref: refs/heads/main"（目前在哪個分支）
├── config              ← 設定檔（INI 格式）：[core]、[user] ...
├── description         ← 給 GitWeb 用，可忽略
├── index               ← 暫存區（二進位檔，第 6 節）
├── objects/
│   ├── 47/0eed5fa84c...    ← loose object：目錄名 = SHA-1 前 2 碼，檔名 = 後 38 碼
│   ├── dc/dc23b...
│   ├── info/
│   └── pack/               ← git gc 之後，物件會被打包到這裡（第 8 節）
│       ├── pack-<sha>.pack
│       └── pack-<sha>.idx
├── refs/
│   ├── heads/              ← 分支：每個檔案內容就是一個 commit 的 SHA-1
│   │   └── main
│   └── tags/               ← 標籤
└── packed-refs         ← git pack-refs 之後，很多 ref 會被合併寫在這一個檔案
```

`pygit init` 只建立必要的東西；其餘（hooks、logs 等）真的 git 會在需要時自己補。

## 3. 物件格式與 SHA-1

### 3.1 所有物件共通的格式

每個物件存起來（未壓縮時）是：

```
<型別> <內容長度>\0<內容>
```

`\0` 是一個值為 0 的位元組。這整串資料算 SHA-1 就是物件的名字，再用 zlib 壓縮後寫到 `.git/objects/xx/yyyy...`。

驗證一下。內容是 `hello` 加換行，共 6 個位元組：

```console
$ printf 'blob 6\0hello\n' | shasum
ce013625030ba8dba906f756967f9e9ca394464a  -
$ echo hello | pygit hash-object --stdin
ce013625030ba8dba906f756967f9e9ca394464a
```

用 Python 只要三行（這也是 `objects.py` 的核心）：

```python
import hashlib
data = b"hello\n"
print(hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest())
```

### 3.2 blob

就是檔案原始內容，不含檔名、不含權限。兩個不同名字但內容相同的檔案，共用同一個 blob。

### 3.3 tree

tree 的內容是一連串的項目，每個項目是：

```
<八進位權限> <檔名>\0<20 位元組的 SHA-1（不是 40 個字元的十六進位，是 20 個原始位元組）>
```

前面 demo 的根目錄 tree 的原始位元組：

```
00000000: 3130 3036 3434 202e 6769 7469 676e 6f72  100644 .gitignor
00000010: 6500 397b 4a76 24e3 5fa6 0563 a9c0 3b12  e.9{Jv$._..c..;.
00000020: 13d9 3f7b 6546 3130 3036 3434 2068 656c  ..?{eF100644 hel
00000030: 6c6f 2e70 7900 3ef8 23c8 1a89 f805 72a1  lo.py.>.#.....r.
00000040: 5cfe 8428 f3b3 e05f 8a8a                 \..(..._..
```

拆開來看：

```
"100644" + " " + ".gitignore" + \0 + 397b4a76...3f7b6546 (20 bytes)
"100644" + " " + "hello.py"   + \0 + 3ef823c8...5f8a8a   (20 bytes)
```

常見的權限值（mode）：

| mode | 意思 |
| --- | --- |
| `100644` | 一般檔案 |
| `100755` | 可執行檔 |
| `120000` | symlink（blob 的內容是連結目標的路徑） |
| `040000` | 子資料夾（寫進 tree 時開頭的 0 會省略，變成 `40000`） |
| `160000` | gitlink（submodule） |

**排序有個小陷阱**：項目依檔名的位元組排序，但資料夾要當作檔名後面多了一個 `/` 來排。所以名字 `a`（資料夾）、`a-b`、`a.b` 的順序是 `a-b`、`a.b`、`a/`。排錯的話 hash 就跟真的 git 不一樣，pygit 有專門的測試用 `git mktree` 驗證這件事。

### 3.4 commit

commit 是純文字：

```
tree 1e42028450d3c33a8437d68272a27b4583622dcb
parent 470eed5fa84c97baed7cddd828d98a160f8931dc
author Ada Chen <ada@example.com> 1772416800 +0800
committer Ada Chen <ada@example.com> 1772416800 +0800

say world
```

- 第一個 `tree` 是這次快照的根資料夾。
- 沒有 `parent` 是第一個 commit（root commit）；有兩個 `parent` 是 merge commit。
- `1772416800` 是 Unix 時間戳（1970-01-01 起算的秒數），`+0800` 是時區（台灣）。
- 空白行之後就是 commit 訊息。

因為 commit 的名字是整段文字的 SHA-1，而它又包含 parent 的 SHA-1，所以**改動任何一個舊 commit，後面所有 commit 的名字都會變**。這就是歷史不能被悄悄竄改的原因。

### 3.5 tag（annotated）

```
object 470eed5fa84c97baed7cddd828d98a160f8931dc
type commit
tag v1.0
tagger Ada Chen <ada@example.com> 1772416800 +0800

release 1.0
```

輕量 tag（`pygit tag v1`）根本不是物件，只是 `refs/tags/v1` 這個檔案，內容是某個 commit 的 SHA-1。

## 4. refs 與 HEAD

分支只是一個小文字檔：

```console
$ cat .git/refs/heads/main
dcdc23b3f5a1... （40 個字元）
```

`git commit` 做的事：建立新的 commit 物件（parent 是現在的 HEAD 指向的 commit），然後把目前分支的那個檔案改成新的 SHA-1。分支「往前走」就是這麼簡單。

`HEAD` 通常是「符號參照」，指向一個分支：

```
HEAD ──▶ refs/heads/main ──▶ dcdc23b（commit）
```

```console
$ cat .git/HEAD
ref: refs/heads/main
```

如果 HEAD 檔案裡直接是 40 個字元的 SHA-1，就是 **detached HEAD**（切換到某個 commit 而不是分支）：

```
HEAD ──▶ dcdc23b（commit）
```

`pygit rev-parse` 的規則（在 `revparse.py`）：

```
HEAD~2      ← 往第一個 parent 走 2 次
HEAD^       ← 第一個 parent；HEAD^2 是 merge commit 的第二個 parent
v1^{tree}   ← 把 tag 剝開、取得 commit 的 tree
HEAD:a.txt  ← HEAD 那個 commit 的 tree 裡，a.txt 這個 blob
abc1234     ← SHA-1 前幾碼（至少 4 碼，只要沒有歧義）
```

`git pack-refs` 會把很多 ref 合併到 `.git/packed-refs` 這個文字檔（每行 `<sha> <ref 名>`），pygit 讀得懂，刪除分支時也會一併從裡面移除。

## 5. 一次 `add` + `commit` 到底做了什麼

```
        工作區                 index (暫存區)               物件資料庫
   ┌──────────────┐  pygit add  ┌───────────────┐          ┌───────────┐
   │ hello.py     │ ──────────▶ │ hello.py      │ ───────▶ │ blob 3ef8 │
   └──────────────┘  ① 存 blob  │  → blob 3ef8  │  ② 寫入  └───────────┘
                     ② 記在 index└───────────────┘
                                        │ pygit commit
                                        ▼ ③ 由 index 建 tree
                                  ┌───────────┐     ┌───────────┐
                                  │ tree 1e42 │ ◀── │ commit dcd│ ④ 建 commit
                                  └───────────┘     └───────────┘
                                                          │ ⑤ 移動 refs/heads/main
                                                          ▼
```

1. `add`：把檔案內容存成 blob 物件。
2. `add`：在 index 裡記下「路徑 → blob 的 SHA-1、權限、檔案狀態」。
3. `commit`：把 index 轉成一層一層的 tree 物件（從最深的資料夾往上，`Repo.write_tree`）。
4. `commit`：建立 commit 物件，指向根 tree 與目前的 HEAD。
5. `commit`：把目前分支的 ref 改成新 commit。

所以 commit 的內容是**index 的內容**，不是工作區的內容。這就是為什麼 `add` 之後再改檔案，那些新改動不會進這次 commit。

## 6. index 檔案的二進位格式

`.git/index` 就是暫存區。pygit 讀寫的是 version 2：

```
┌───────────────────────── header（12 位元組）─────────────────────────┐
│ "DIRC" (4)  │ 版本號 = 2 (4, big-endian) │ 項目數量 N (4)            │
├───────────────────────── 項目 × N ───────────────────────────────────┤
│ ctime 秒 (4)  │ ctime 奈秒 (4)  │ mtime 秒 (4)  │ mtime 奈秒 (4)      │
│ dev (4)       │ ino (4)         │ mode (4)      │ uid (4)             │
│ gid (4)       │ 檔案大小 (4)    │ SHA-1 (20)                          │
│ flags (2)     │ 路徑（不定長，UTF-8）│ \0 補齊到 8 的倍數（至少 1 個）│
├───────────────────────── extensions（選用）──────────────────────────┤
│ 4 字元簽名 │ 大小 (4) │ 資料 ...   例如 "TREE"（快取 tree 的 SHA-1）  │
├──────────────────────────────────────────────────────────────────────┤
│ 以上全部內容的 SHA-1（20 位元組），用來偵測檔案損壞                  │
└──────────────────────────────────────────────────────────────────────┘
```

- 所有數字都是 **big-endian**。
- 項目**依路徑的位元組順序排序**。
- `flags` 的低 12 位元是路徑長度（超過 4095 時填 0xFFF，讀取時找 `\0`）；第 12–13 位元是 stage（衝突時才不為 0）。
- **補齊（padding）**：一個項目從頭到路徑結束共 `62 + 路徑長度` 位元組，接著補 1 到 8 個 `\0`，讓總長度是 8 的倍數。公式：`總長度 = (62 + 路徑長度 + 8) & ~7`。

用 demo 的 index 舉例（`xxd .git/index`）：

```
00000000: 4449 5243 0000 0002 0000 0002 6aba e721  DIRC........j..!
          └DIRC─┘ └版本 2─┘ └項目數 2┘ └ctime 秒┘
00000010: 119a 3d33 6aba e721 119a 3d33 0100 000f  ..=3j..!..=3....
          └ctime奈秒┘└mtime 秒┘ └mtime奈秒┘└ dev ┘
00000020: 004a 67ff 0000 81a4 0000 01f5 0000 0000  .Jg.............
          └ ino  ┘ └mode ┘  └ uid ┘  └ gid ┘
00000030: 0000 0006 397b 4a76 24e3 5fa6 0563 a9c0  ....9{Jv$._..c..
          └size=6┘ └ SHA-1（前 12 位元組）...
00000040: 3b12 13d9 3f7b 6546 000a 2e67 6974 6967  ;...?{eF...gitig
          ...SHA-1 結束┘ └flags=10（路徑長度）└ ".gitig..."
```

`mode` 的 `0x81a4` 就是八進位 `100644`。這個項目長 `62 + 10 = 72`，補到 80 位元組（8 個 `\0`）。

### status 怎麼知道檔案有沒有被改？

如果每次 `status` 都把所有檔案重新讀一遍、算 SHA-1，大專案會很慢。所以 index 記了每個檔案的 **stat 快取**（大小、mtime、inode）。判斷邏輯：

```
lstat(檔案) 的大小、mtime、inode 都跟 index 記的一樣？
   ├─ 是 → 視為沒改（很快，不用讀檔案）
   └─ 否 → 讀檔案內容算 SHA-1，跟 index 裡的 SHA-1 比
            ├─ 相同 → 沒改（只是被 touch 過）
            └─ 不同 → 已修改
```

有個細節叫 **racy git**：如果檔案是在 index 寫入的「同一個時間刻度」內被改的，大小與 mtime 可能剛好都沒變。所以 mtime 不比 index 檔本身更早的項目，pygit 一律重新算 hash（`worktree.stat_matches`）。

`status` 顯示的兩個 XY 欄位，就是兩次比較的結果：

```
   HEAD 的 tree   ──比較──   index   ──比較──   工作區
                   X（已暫存）        Y（未暫存）
```

## 7. checkout 怎麼切換分支

切換分支就是把「現在 HEAD 的 tree」變成「目標分支的 tree」：

```
   目前 tree  ──差異──▶  目標 tree
   對每個有差異的路徑：
     ① 檢查衝突：index 或工作區在這個路徑有沒有未提交的修改？
                 目標要新增的檔案，工作區是否已經有一個未追蹤的同名檔案？
        有 → 拒絕，什麼都不動（跟 git 一樣）
     ② 目標沒有這個檔案 → 刪除檔案（資料夾空了也一併刪）
     ③ 目標有 → 寫出檔案內容（設好可執行權限或建立 symlink）、更新 index
   最後 → 把 HEAD 改成指向新分支
```

只有「兩邊 tree 不一樣的路徑」才需要檢查。所以你在兩個分支上都沒動過的檔案，就算有本地修改，切換也會照樣保留。

## 8. Packfile：讓 Git 儲存空間變小

每個 loose object 一個檔案，物件多了會很浪費（每個檔案至少佔一個磁碟區塊、修改一行就得存整個新檔案）。`git gc` 會把它們打包：

```
pack-xxxx.pack                              pack-xxxx.idx（索引）
┌──────────────────────────┐               ┌────────────────────────────┐
│ "PACK" 版本 物件數        │               │ magic \377tOc、版本 2       │
├──────────────────────────┤               │ fanout[256]：第一個位元組 ≤ i │
│ 物件 1：型別+大小 │ zlib   │  ◀── 偏移 ───│   的物件有幾個（加速二分搜尋） │
│ 物件 2：型別+大小 │ zlib   │               │ 排序過的 SHA-1 × N           │
│ 物件 3：OFS_DELTA ...     │               │ CRC32 × N                    │
│ ...                      │               │ 在 pack 裡的偏移 × N         │
├──────────────────────────┤               │ pack 的 SHA-1、idx 的 SHA-1  │
│ 整個 pack 的 SHA-1        │               └────────────────────────────┘
└──────────────────────────┘
```

**查找一個物件**：用 SHA-1 第一個位元組查 fanout 找到範圍，在排序過的 SHA-1 表二分搜尋，得到它在 `.pack` 裡的偏移，再跳過去讀（`pack.PackFile.find` / `read_at`）。

**每個物件的開頭**：第一個位元組的第 4–6 位元是型別（1 commit、2 tree、3 blob、4 tag、6 OFS_DELTA、7 REF_DELTA），其餘位元組（可變長度）是解壓後的大小，後面接 zlib 壓縮的資料。

**Delta（差異壓縮）** 是 pack 省空間的祕訣：一個檔案改了幾行，就只存「以那個舊版本為基底，怎樣改成新版本」：

```
新版本 = 基底 + 一串指令
  copy(offset, size)  ← 從基底複製一段
  insert(資料)        ← 插入新的位元組
```

基底有兩種指定方式：`OFS_DELTA` 用「往回幾個位元組」，`REF_DELTA` 用基底的 SHA-1。基底本身也可能是 delta，所以要一路追到不是 delta 的物件，再依序套回來（`pack.apply_delta`）。

pygit 只讀 pack，不寫 pack；它新建立的物件一律寫成 loose object。這樣不影響互通：git 兩種都讀得懂。

## 9. diff：Myers 演算法

`pygit diff` 要找出「舊檔案怎麼用最少的刪除與新增變成新檔案」。這等價於找兩份行列表的最長共同子序列（LCS）。pygit 用的是 Eugene Myers 1986 年的 O(ND) 演算法（`diff.py` 的 `myers_ops`），N 是總行數、D 是差異的行數，所以「改動很少的大檔案」很快。

把編輯過程想成在方格圖上走路：往右一步是刪除舊檔的一行，往下一步是新增新檔的一行，兩邊那行相同時可以免費斜著走一步。目標是用最少的「右」與「下」走到右下角。例如把 `A B C A B B A` 變成 `C B A B A C`：

```
- A
- B
  C
+ B
  A
  B
- B
  A
+ C
```

這是其中一種最短的編輯（5 個 `-` / `+`）。

演算法逐步嘗試「最多 D 步非斜向移動」，找出最先抵達右下角的路徑。得到 `=`（相同）、`-`（刪除）、`+`（新增）的序列後，再把附近的變動合併成 hunk，每個 hunk 前後留 3 行上下文，寫成 `@@ -起始,行數 +起始,行數 @@` 的標頭。

## 10. 這些對應到 pygit 的哪裡

| 概念 | 檔案 |
| --- | --- |
| 物件格式、SHA-1 | `objects.py`、`odb.py` |
| refs、HEAD、packed-refs | `refs.py`、`revparse.py` |
| index 二進位格式 | `index.py` |
| stat 快取、status、checkout | `worktree.py` |
| packfile、delta | `pack.py` |
| Myers、unified diff | `diff.py` |
| `.gitignore` | `ignore.py` |

想從哪裡開始讀？建議順序：`objects.py` → `odb.py` → `refs.py` → `index.py` → `repo.py` → `worktree.py` → `commands.py`。每個檔案都不長。

---

## 已知限制

- 沒有 merge、rebase、cherry-pick、stash、reset、remote（fetch / push / clone）。遇到有衝突（stage 不為 0）的 index 會直接報錯。
- 只寫 loose object，不會產生 packfile（但讀得懂）。不支援 SHA-256 repo、partial clone、shallow、alternates、submodule 的內容。
- index：讀 v2 與 v3；v4、split index（`link` extension）會報錯。寫出的一律是 v2，並且不寫 extension（git 會自行重建）。
- `status` 不做 rename 偵測，也沒有 `--ignored`；untracked 目錄一律折疊成 `dir/`（等同 `-unormal`）。`status` / `diff` 的比對假設 `core.filemode=true`、`core.symlinks=true`（Unix / macOS）。
- `diff` 只支援「工作區 vs index」與 `--cached`（index vs HEAD），沒有 commit 對 commit；也沒有 rename、color、`--stat`。差異極大的兩個檔案（編輯距離超過 4000 行）會直接輸出「整份替換」，仍是合法的 diff 但不一定最短。
- `log --graph` 是簡易版，只畫欄位不畫分岔連線；`log` 沒有 `--format`、`--author` 等篩選。`--decorate` 只在終端機或明確指定時顯示。
- 不讀 `.gitattributes`、hooks、reflog（不會寫 reflog）；換行符號不轉換。
- `.gitignore` 比對涵蓋常見規則，但不讀全域的 `core.excludesFile`。
- 路徑引號（`"\344\270\255"`）與 git 預設的 `core.quotepath=true` 一致，不支援關閉。
- 只在 macOS / Linux 測試（CI 就是這兩個），沒有針對 Windows 處理。

## 授權

MIT License，詳見 [LICENSE](LICENSE)。Copyright (c) 2026 useless-husband。
