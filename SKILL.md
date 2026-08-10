---
name: zotero-cli
description: "Import papers into Zotero or check the library via SQLite CLI."
version: 2.0.0
author: q2635
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [zotero, bibtex, papers, import, dedup, arxiv]
    related_skills: [obsidian-research, inbox-webclip]
---

# zotero-cli

SQLite 直读直写的 Zotero 库管理 CLI。读操作不需要 Zotero 开着；写操作要求 Zotero 关闭。

## 触发条件

用户要求导入论文到 Zotero、查论文是否已在库、整理分类、导出 BibTeX。

## 第一步：定位工具

- 优先用 PATH 里的 `zotero-cli`；找不到则从仓库根目录跑 `python3 zotero.py`
- 仓库：https://github.com/LittleDrinks/zotero-cli

## 第二步：跑命令

```bash
zotero-cli status            # 确认库可读、Zotero 未运行
zotero-cli search "关键词"    # 查重/搜索
zotero-cli collections       # 列分类
zotero-cli import pdf a.pdf --collection "分类名"
zotero-cli import arxiv 2608.04003 --collection "分类名"
zotero-cli meta-check        # 扫缺 date 的条目
zotero-cli export-bibtex --out refs.bib
```

## 第三步：按输出继续

- **import 报 "Zotero is running"**：请用户关闭 Zotero 后重跑
- **import 输出 SKIP**：条目已在库或 PDF 无效，向用户说明原因
- **import 输出 IMPORTED**：向用户报告条目 key 和标题，确认已归档
- **任务含测试**：完成后清理测试条目（主条目 + 附件条目 + storage 目录 + 临时分类）

## 注意

- 导入的 PDF 会被校验并自动提取标题、查重、补 arXiv 元数据，无需人工干预
- 更复杂场景（批量元数据核对、OpenReview 下载、storage 修链）走 obsidian-research skill

## 局限

- 只 journalArticle 类型；DOI 论文无自动元数据；扫描版 PDF 会 SKIP

完整说明（安装、导入流程原理、踩坑记录、配置）见 README.md。
