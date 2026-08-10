# zotero-cli

命令行管理本地 Zotero 库。SQLite 直读直写——**读操作不需要 Zotero 开着；写操作要求 Zotero 关闭**（SQLite 写锁安全）。

## 为什么做这个

Zotero 自带的接口都有硬伤：本地 HTTP API（`localhost:23119`）只读；connector 写操作要求桌面端运行且只能导入到"当前选中分类"。这个工具直接读写 `zotero.sqlite`：搜索、查重、导入 arXiv 论文（API 验证元数据）、把 PDF 归档到正确的 storage 目录——全程不依赖 Zotero 进程。

## 安装

```bash
git clone https://github.com/LittleDrinks/zotero-cli.git
cd zotero-cli
# 可选：加入 PATH
ln -s "$PWD/zotero.py" ~/.local/bin/zotero-cli
```

## 环境要求

- Python 3.10+（零第三方依赖，stdlib only）
- `pdftotext`（poppler-utils）——提取 PDF 首页标题
- 导入时 Zotero 需关闭（SQLite 写锁）

## 使用

```bash
# 库健康检查
zotero-cli status

# 标题搜索（查重前置）
zotero-cli search "attention is all you need"

# 列出分类
zotero-cli collections

# 导入本地 PDF（自动提标题、查重、补元数据）
zotero-cli import pdf paper.pdf --collection "AI4Science"

# 导入 arXiv 论文（API 验证标题，自动下载 PDF）
zotero-cli import arxiv 2608.04003 --collection "AI4Science"

# 扫描缺 date 元数据的条目
zotero-cli meta-check

# 导出 BibTeX
zotero-cli export-bibtex --out references.bib
```

## 导入流程

1. **校验 PDF**：检查 `%PDF-` magic 和文件大小（>50KB）。HTML 错误页、下载占位文件直接被拒。
2. **提取标题**：`pdftotext` 读第一页——用真实标题，不信文件名（文件名经常是谎言）。
3. **查重**：用标题前 2-3 个长词拼 LIKE 短语精确匹配。仅当"标题完全一致且已有 PDF 附件"时判定已存在并跳过。
4. **arXiv 元数据**：从文件名或首页文本找 arXiv ID，走 API `id_list` 验证标题/作者。绝不信凭记忆编的 ID。
5. **入库**：写入主条目 + 附件条目两条 items 行；storage 目录名 = **附件条目 key**（不是主条目 key）；作者按 last/first 拆分。
6. **归分类**：按名称找 collection，不存在则创建，再挂载。

## 踩坑记录（为什么这些坑存在）

- **Zotero 运行检测**：`tasklist.exe` 输出是 GBK 编码，UTF-8 解码会崩溃。按 bytes 匹配或 `decode('gbk')` 处理。
- **arXiv 网络**：arXiv API 走代理会 SSL EOF——必须 `ProxyHandler({})` 直连。注意 OpenReview 相反（必须走代理），本工具不含 OpenReview。
- **附件 key ≠ 主条目 key**：storage 目录名必须等于附件条目的 key（Zotero 约定）。验证：
  `SELECT i.key FROM items i JOIN itemAttachments ia ON ia.itemID=i.itemID WHERE ia.parentItemID=<主条目>`
- **9p 挂载缓存**：写完 storage 后 WSL 侧 `os.path.exists` 可能间歇返回 False——以 Windows 侧 `cmd.exe` 验证为准。
- **附件条目不级联删除**：删主条目不会删附件条目（itemAttachments 的 itemID 是附件自己的主键，parentItemID 才是外键）。手动删除顺序：先删 itemAttachments 行，再删附件 items 行。
- **写锁**：Zotero 运行中直写 SQLite 有损坏风险。工具内建检查，报 "Zotero is running" 时先关闭 Zotero。

## 配置

复制 `.env.example` 为 `.env`，填入你的 Zotero 数据目录：

```bash
cp .env.example .env
```

```env
# .env
ZOTERO_DB_PATH=/path/to/zotero.sqlite
ZOTERO_STORAGE=/path/to/storage
```

`.env` 已被 `.gitignore` 忽略，不会上传仓库。也可用环境变量或每条命令传 `--db` / `--storage`。不设置时默认 `~/Zotero/`（Zotero 标准数据目录）。

## 安全设计

- Zotero 运行时拒绝写操作（tasklist.exe 检查）
- 导入文件校验 `%PDF-` magic + 大小 >50KB，HTML 错误页直接拒
- 已存在条目跳过，绝不重复导入

## 局限

- 只处理 journalArticle 类型；book/conferencePaper 等需手动扩展 meta
- 元数据依赖 arXiv API；DOI 论文暂不支持自动元数据（手动 `--date`）
- 扫描版 PDF 无文字层 → pdftotext 提不出标题，会 SKIP 并提示
- SQLite schema 是 Zotero 内部实现，Zotero 大版本升级需适配

## License

MIT
