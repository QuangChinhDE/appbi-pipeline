"""Build an offline manual with embedded screenshots.

Install Markdown 3.7 into a virtual environment, then run this script.
The optional local .cache/manual-tools directory is used for this workspace.
"""
from pathlib import Path
import base64
import html
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / '.cache' / 'manual-tools'))
import markdown

source = (HERE / 'README.md').read_text(encoding='utf-8')
evidence = HERE / 'kiem-chung.md'
if evidence.exists():
    source += '\n\n' + evidence.read_text(encoding='utf-8')
md = markdown.Markdown(extensions=['tables', 'fenced_code', 'toc', 'sane_lists'],
                       extension_configs={'toc': {'toc_depth': '2-2'}})
body = md.convert(source)

def embed(match):
    path = HERE / match.group(1)
    if not path.is_file():
        raise FileNotFoundError(f'Missing manual screenshot: {path}')
    return 'src="data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode() + '"'

body = re.sub(r'src="(images/[^\"]+)"', embed, body)
css = '''
:root{--ink:#17242e;--muted:#586674;--line:#d7dfe5;--blue:#195b89;--paper:#fff;--page:#f3f6f8}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:28px}
body{margin:0;background:var(--page);color:var(--ink);font:16px/1.75 "Segoe UI",Arial,sans-serif}
aside{position:fixed;inset:0 auto 0 0;width:280px;padding:32px 24px;overflow:auto;background:#132f42;color:#e4eef5}
.brand{font-size:22px;font-weight:750;letter-spacing:.02em}.edition{font-size:12px;color:#b6ccdb;margin:6px 0 25px}
nav ul{list-style:none;padding:0}nav li{margin:0 0 12px}nav a{color:#e4eef5;font-size:13px;line-height:1.5;display:block;text-decoration:none}
nav a:hover{color:#fff;text-decoration:underline}.tools{display:flex;gap:8px;margin-top:28px}
button{border:1px solid #9ab4c7;background:transparent;color:#fff;border-radius:5px;padding:9px 13px;cursor:pointer;font:inherit;font-size:13px}
main{margin-left:280px;padding:55px clamp(28px,5vw,88px) 90px;max-width:1540px;background:var(--paper);min-height:100vh}
.eyebrow{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.14em;color:var(--blue);margin-bottom:14px}
h1{font-size:clamp(32px,3.2vw,47px);line-height:1.18;letter-spacing:-.025em;margin:0 0 30px;max-width:900px;color:#000}
h2{font-size:28px;line-height:1.3;letter-spacing:-.02em;margin:64px 0 24px;color:#000}
h3{font-size:20px;line-height:1.4;margin:35px 0 14px;color:#000}p{margin:14px 0}a{color:#165e91;text-underline-offset:3px}
strong{font-weight:650}ul,ol{padding-left:25px}li{margin:9px 0}li p{margin:5px 0}
code{font:14px/1.5 Consolas,"Cascadia Code",monospace;background:#eff3f6;padding:2px 5px;border-radius:3px;overflow-wrap:anywhere}
pre{background:#f0f4f7;border:1px solid #d7e1e9;border-radius:6px;padding:20px 22px;overflow:auto;margin:22px 0;white-space:pre-wrap;overflow-wrap:anywhere}
pre code{background:none;padding:0;font-size:13px;line-height:1.7;border-radius:0}
table{width:100%;border-collapse:collapse;margin:24px 0;font-size:14px;line-height:1.65;table-layout:auto}
th{background:#21475f;color:white;text-align:left;font-weight:650}td,th{padding:13px 15px;border:1px solid #d9d9d9;vertical-align:middle}
tr:nth-child(even) td{background:#f3f6f8}td code{font-size:12px;white-space:normal}
img{display:block;width:100%;height:auto;border:1px solid #d5dde5;border-radius:5px;margin:30px auto 8px;cursor:zoom-in}
p:has(>em:only-child){font-size:13px;color:var(--muted);margin:8px 0 30px}
dialog{border:0;padding:8px;max-width:98vw;max-height:98vh;background:white}dialog::backdrop{background:#071c2ddd}
dialog img{width:auto;max-width:95vw;max-height:90vh;margin:0;object-fit:contain;cursor:zoom-out}
footer{margin-top:65px;padding-top:20px;color:var(--muted);font-size:12px}
@media(max-width:980px){aside{position:relative;width:auto;padding:22px}nav ul{columns:2}main{margin:0;padding:35px 22px}h1{font-size:33px}table{font-size:13px}td,th{padding:10px}}
@media print{@page{size:A4;margin:17mm 16mm 18mm}body{font-size:10pt;line-height:1.6;background:white}aside,.eyebrow,dialog{display:none}main{margin:0;padding:0;max-width:none}h1{font-size:27pt}h2{font-size:18pt;break-before:page;margin-top:0}h3{font-size:13pt;break-after:avoid}p,li{orphans:3;widows:3}pre{font-size:8pt;break-inside:avoid;padding:10px}pre code{font-size:8pt}table{font-size:8.5pt}td,th{padding:7px 8px}thead{display:table-header-group}tr,img{break-inside:avoid}img{max-height:150mm;object-fit:contain}a{color:inherit}footer{display:none}}
'''
document = f'''<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hướng dẫn cài đặt và sử dụng AppBI Pipeline</title><style>{css}</style></head>
<body><aside><div class="brand">AppBI Pipeline</div><div class="edition">CÀI ĐẶT VÀ SỬ DỤNG<br>08.10.2026 · source 3937ef9</div>
<nav aria-label="Mục lục">{md.toc}</nav><div class="tools"><button onclick="window.print()">In tài liệu</button></div></aside>
<main><div class="eyebrow">Sổ tay cài đặt và vận hành</div>{body}<footer>AppBI Pipeline · Tài liệu tiếng Việt · Ảnh giao diện chụp từ ứng dụng local</footer></main>
<dialog id="zoom" aria-label="Ảnh minh họa phóng to"><img alt="Ảnh minh họa phóng to"></dialog>
<script>const d=document.getElementById('zoom');document.querySelectorAll('main img').forEach(i=>{{i.tabIndex=0;i.onclick=()=>{{d.querySelector('img').src=i.src;d.querySelector('img').alt=i.alt;d.showModal();}};i.onkeydown=e=>{{if(e.key==='Enter')i.click();}}}});d.onclick=()=>d.close();</script></body></html>'''
target = HERE / 'Huong-dan-AppBI-Pipeline.html'
target.write_text(document, encoding='utf-8')
print(f'Created {target.name}: {target.stat().st_size:,} bytes; {len(re.findall(r"<img", body))} screenshots')
