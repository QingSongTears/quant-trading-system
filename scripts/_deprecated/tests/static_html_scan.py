"""
静态扫描所有 HTML 模板的 Jinja2 陷阱
"""
import re
from pathlib import Path

TEMPLATES = Path('src/web/templates')

print('=== Jinja2 陷阱扫描 ===\n')
for html_file in sorted(TEMPLATES.glob('*.html')):
    content = html_file.read_text(encoding='utf-8')
    size_kb = len(content) / 1024
    print(f'\n--- {html_file.name} ({size_kb:.1f}KB, {content.count(chr(10))+1} lines) ---')

    # 1. JS 字面量中可能误解析的 }}
    # 思路：找到所有 <script> 块，检查每个块是否在 {% raw %} 之外且含 }}
    issues = []
    for m in re.finditer(r'{% raw %}(.*?){% endraw %}', content, re.DOTALL):
        pass  # raw 块是安全的
    # 提取所有 script 块（包括被 raw 包裹的）
    script_blocks = []
    for m in re.finditer(r'<script[^>]*>(.*?)</script>', content, re.DOTALL):
        script_blocks.append((m.start(), m.end(), m.group(1)))

    # 检查每个 script 块的相对位置：如果该 script 块在最近的 raw 块之内则安全
    raw_ranges = [(m.start(), m.end()) for m in re.finditer(r'{% raw %}.*?{% endraw %}', content, re.DOTALL)]

    for s, e, js in script_blocks:
        # 是否在 raw 块之内？
        in_raw = any(rs <= s and e <= re_ for rs, re_ in raw_ranges)
        if in_raw:
            continue
        # 查找未保护的 JS }} 字面量
        # 风险点: object.method() }, function() { return {x: 1} } 等
        if '}}' in js and '{%' in js:
            # 有 Jinja 模板的 JS 块，}} 一定有风险
            issues.append('含 {{...}} 与 }} 同存的 JS 块（未用 raw 包裹）')

    # 2. {{x|tojson}} 模式计数
    tojson_count = len(re.findall(r'\{\{[^}]*\|\s*tojson', content))
    print(f'  tojson 用法: {tojson_count} 处')

    # 3. 内联 onclick
    onclick_count = len(re.findall(r'\bonclick\s*=', content))
    print(f'  内联 onclick: {onclick_count} 处')

    # 4. (x or 0) 防御 None 注入
    none_guarded = len(re.findall(r'\(\s*[a-zA-Z_][\w.]*\s+or\s+', content))
    print(f'  None 防御 (x or ...): {none_guarded} 处')

    # 5. CDN 引用
    cdn_refs = re.findall(r'\{\{\s*cdn\.[\w]+\s*\}\}', content)
    print(f'  CDN 引用: {len(cdn_refs)} 处 unique={set(cdn_refs)}')

    # 6. {{ x | safe }} 不安全用法
    unsafe = re.findall(r'\{\{[^}]*\|\s*safe[^}]*\}\}', content)
    if unsafe:
        print(f'  ⚠️  | safe 用法: {len(unsafe)} 处')
        for u in unsafe[:3]:
            print(f'     {u}')

    # 7. 检查未闭合的 Jinja 块
    for tag in ['{% if', '{% for', '{% block', '{% raw']:
        n = content.count(tag)
        end_tag = tag.replace('{%', '{% end').replace('if', 'endif').replace('for', 'endfor').replace('block', 'endblock').replace('raw', 'endraw')
        end_n = content.count(end_tag)
        if n != end_n:
            print(f'  ⚠️  {tag}={n} 但 {end_tag}={end_n} (不平衡)')

    # 8. 检查 {{ undefined_var }} 风险
    # 找未保护的 {{ }}
    for m in re.finditer(r'\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}', content):
        var = m.group(1)
        # 跳过合法用法: | tojson, | safe, | round, 等
        # 实际是看 raw context

    if issues:
        print(f'  ⚠️  风险: {issues}')

print('\n=== 完成 ===')
