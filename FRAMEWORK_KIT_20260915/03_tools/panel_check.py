# -*- coding: utf-8 -*-
"""面板模块完整性自检（#68 首建·#85 升 13 段·#101 升 16 段）——防"家传模块被挤掉"复发。
用法: python3 scripts/panel_check.py [面板路径]   默认=现行面板（#106 起双位扫描 docs/ 与 reports/HPE/<日期>/，取日期+_V 最新）
判据: ⓪-⑮ 编号连续 16 段 + 家传模块关键词齐全（模块清单=并集 #66+#69+#85+#101）
"""
import io, re, sys, os
import glob, re as _re
# #106 迁移（NVDA 式日期夹）后双位扫描：docs/ 历史位 + reports/HPE/<日期>/ 新位
cands = (glob.glob('docs/HPE_OPERATION_PANEL_*.html')
         + glob.glob('reports/HPE/*/HPE_OPERATION_PANEL_*.html'))
def _key(f):
    b = os.path.basename(f)
    m = _re.search(r'(2026\d{4})(?:_V(\d+))?', b)
    d = m.group(1) if m else '00000000'
    v = int(m.group(2) or 0) if m else 0
    return (d, v, b)
panels = sorted(cands, key=_key)
default = panels[-1] if panels else ''
path = sys.argv[1] if len(sys.argv) > 1 else default
if not path:
    print('PANEL CHECK FAIL: 找不到日期命名的面板文件'); sys.exit(1)
t = io.open(path, encoding='utf-8').read()
REQUIRED = ['判据记分卡','平台结构图','四轴价格地图','价格阶梯','AAWS 引擎状态','仓位计划',
            '周节律','期权执行标准','闸门','关键日历','数字锚速查','大买剧本','期权链驾驶舱']
REQUIRED_V2 = ['薅羊毛做T','应急预案','消息']  # 仅 16 段面板必查（13 段旧面板豁免 #101）
ORDER = '⓪①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯'  # #102：⑯判据回测；合法长度={17 全序,16(⓪-⑮ V2),13(⓪-⑫ 旧版)}
hs = re.findall(r'<h2>([⓪①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])[^<]*', t)
seq = ''.join(hs)
errs = []
_VALID_LENS = {len(ORDER), 16, 13}
if not (seq == ORDER or (ORDER.startswith(seq) and len(seq) in _VALID_LENS)): errs.append(f'编号序列异常: {seq!r}（应为 ⓪-⑯ 全序/⓪-⑮ V2/⓪-⑫ 旧版）')
for k in REQUIRED:
    if k not in t: errs.append(f'缺模块: {k}')
if '⑬' in seq:
    for k in REQUIRED_V2:
        if k not in t: errs.append(f'V2 缺模块: {k}')
if '⑯' in seq and '判据回测' not in t: errs.append('V3 缺模块: 判据回测')
for k in ['风险眼','摘要卡']:
    if k not in t: errs.append(f'缺家传件: {k}')
if errs:
    print(f'PANEL CHECK FAIL: {path}'); [print(' -', e) for e in errs]; sys.exit(1)
n_sec = len(hs)
print(f'PANEL CHECK OK: {path} — {n_sec} 段编号连续 + 家传模块齐全（并集口径 #66+#69+#85+#101；13 段旧面板=前缀合法 #101）')
