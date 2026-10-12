#!/usr/bin/env python3
"""
沉浸式翻译 (BabelDOC) 无头 PDF 双语翻译客户端
复用用户的沉浸式翻译 Pro 会员权限，完全无头执行 PDF 双语排版翻译，
保留原始数学公式、多栏排版与矢量图，并支持直接挂载回 Zotero 库。
"""

import argparse
import glob
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

API_BASE = "https://api2.immersivetranslate.com/zotero"


def find_default_authkey() -> str | None:
    """自动从 Windows Zotero 用户的 prefs.js 读取已配置的沉浸式翻译授权码"""
    # 1. 优先从环境变量读取
    env_key = os.environ.get("IMT_AUTH_KEY") or os.environ.get("TRANSLATE_API_KEY")
    if env_key and env_key.startswith("IMT-"):
        return env_key

    # 2. 检查 .env
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("IMT_AUTH_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")

    # 3. 自动扫描 Windows 宿主机 Zotero profile prefs.js
    pattern = "/mnt/c/Users/*/AppData/Roaming/Zotero/Zotero/Profiles/*/prefs.js"
    for pref_file in glob.glob(pattern):
        try:
            content = Path(pref_file).read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'user_pref\("extensions\.zotero\.immersivetranslate\.authkey",\s*"([^"]+)"\)', content)
            if m:
                return m.group(1).strip()
        except OSError:
            continue
    return None


def request_json(url: str, method: str = "GET", headers: dict = None, body: dict = None) -> dict:
    data_bytes = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data_bytes, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def translate_pdf_headless(
    pdf_path: Path,
    auth_key: str,
    output_path: Path = None,
    target_lang: str = "zh-CN",
    model: str = "kimi+deepseek",
) -> Path:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

    auth_headers = {
        "Authorization": f"Bearer {auth_key}",
        "Content-Type": "application/json",
    }

    # 1. 获取预签名上传地址
    print(f"[*] 正在为 {pdf_path.name} 获取云端预签名上传地址...")
    upload_res = request_json(f"{API_BASE}/pdf-upload-url", method="GET", headers=auth_headers)
    if upload_res.get("code") != 0 or not upload_res.get("data"):
        raise RuntimeError(f"获取上传地址失败: {upload_res}")

    result_data = upload_res["data"].get("result", {})
    upload_url = result_data.get("uploadUrl") or result_data.get("preSignedURL")
    object_key = result_data.get("objectKey")
    if not upload_url or not object_key:
        raise RuntimeError(f"返回的上传地址或 objectKey 为空: {result_data}")

    # 2. 上传文件内容 (PUT binary)
    print(f"[*] 正在上传 PDF 到云端对象存储 ({pdf_path.stat().st_size / 1024 / 1024:.2f} MB)...")
    pdf_bytes = pdf_path.read_bytes()
    put_req = urllib.request.Request(
        upload_url,
        data=pdf_bytes,
        headers={"Content-Type": "application/pdf"},
        method="PUT",
    )
    with urllib.request.urlopen(put_req, timeout=120) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"上传文件失败，HTTP 状态码: {resp.status}")

    # 3. 创建 BabelDOC 翻译任务
    print(f"[*] 正在创建云端 BabelDOC 翻译任务 (模型: {model}, 目标语言: {target_lang})...")
    task_payload = {
        "objectKey": object_key,
        "fileName": pdf_path.name,
        "targetLanguage": target_lang,
        "requestModel": model,
        "pdfOptions": {"conversion_formats": {"html": True}},
        "enhance_compatibility": False,
        "OCRWorkaround": False,
        "autoEnableOcrWorkAround": False,
        "autoExtractGlossary": True,
        "disable_rich_text_translate": False,
    }
    task_res = request_json(f"{API_BASE}/backend-babel-pdf", method="POST", headers=auth_headers, body=task_payload)
    if task_res.get("code") != 0 or not task_res.get("data"):
        raise RuntimeError(f"创建翻译任务失败: {task_res}")

    pdf_id = task_res["data"]
    if isinstance(pdf_id, dict):
        pdf_id = pdf_id.get("pdfId") or pdf_id.get("id")
    print(f"[+] 任务创建成功！任务 ID: {pdf_id}")

    # 4. 轮询进度
    print("[*] 正在等待 BabelDOC 解析与 AI 双语翻译完成...")
    last_progress = -1
    t0 = time.time()
    while True:
        status_res = request_json(f"{API_BASE}/pdf/{pdf_id}/process", method="GET", headers=auth_headers)
        status_data = status_res.get("data", {})
        status = status_data.get("status")
        progress = status_data.get("progress", 0)

        if progress != last_progress:
            print(f"    -> 翻译进度: {progress}% (耗时: {int(time.time() - t0)}s)")
            last_progress = progress

        if status == "success" or progress >= 100:
            break
        if status in ("failed", "error"):
            raise RuntimeError(f"云端翻译失败: {status_data}")

        time.sleep(3)

    # 5. 获取结果临时下载地址
    print("[*] 获取双语对照 PDF 下载直链...")
    result_res = request_json(f"{API_BASE}/pdf/{pdf_id}/temp-url", method="GET", headers=auth_headers)
    if result_res.get("code") != 0 or not result_res.get("data"):
        raise RuntimeError(f"获取下载链接失败: {result_res}")

    download_url = result_res["data"].get("tempUrl") or result_res["data"].get("url")
    if not download_url:
        raise RuntimeError(f"下载 URL 为空: {result_res}")

    # 6. 下载双语对照 PDF
    if not output_path:
        output_path = pdf_path.parent / f"{pdf_path.stem}_bilingual.pdf"

    print(f"[*] 正在下载双语对照 PDF 到: {output_path}...")
    dl_req = urllib.request.Request(download_url)
    with urllib.request.urlopen(dl_req, timeout=120) as resp:
        output_path.write_bytes(resp.read())

    print(f"[✓] 双语排版翻译完成！文件已就绪: {output_path} ({output_path.stat().st_size / 1024 / 1024:.2f} MB)")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="沉浸式翻译 BabelDOC 无头 PDF 双语翻译工具")
    parser.add_argument("pdf", help="待翻译的 PDF 文件路径")
    parser.add_argument("--authkey", help="沉浸式翻译授权码 (默认自动读取本地 Zotero 配置)")
    parser.add_argument("--out", help="输出双语 PDF 文件路径")
    parser.add_argument("--lang", default="zh-CN", help="目标语言代码 (默认: zh-CN)")
    parser.add_argument("--model", default="kimi+deepseek", help="翻译模型 (默认: kimi+deepseek)")
    args = parser.parse_args()

    authkey = args.authkey or find_default_authkey()
    if not authkey:
        print("错误: 未找到沉浸式翻译授权码，请通过 --authkey 指定或在 Zotero 设置中登录沉浸式翻译。", file=sys.stderr)
        sys.exit(1)

    pdf_file = Path(args.pdf).resolve()
    out_file = Path(args.out).resolve() if args.out else None

    translate_pdf_headless(
        pdf_path=pdf_file,
        auth_key=authkey,
        output_path=out_file,
        target_lang=args.lang,
        model=args.model,
    )


if __name__ == "__main__":
    main()
